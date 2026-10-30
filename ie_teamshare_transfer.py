#!/usr/bin/env python3
"""
Inazuma Eleven 1 (NDS) - teamshare w zapisach .sav

Uruchomienie:
    python ie_teamshare_transfer.py            -> okno graficzne (GUI)
    python ie_teamshare_transfer.py --gui      -> okno graficzne (GUI)

Język / Language: przełącznik w oknie albo --lang pl|en (np. python ie_teamshare_transfer.py --lang en)
English option names: --add-team, --slot, --list, --remove-team, --repair

Wiersz poleceń (nazwy ze spacjami w cudzysłowie):
    ZRODLO.sav CEL.sav [-o WYJSCIE.sav]                    skopiuj cały teamshare ze źródła do celu
    ZRODLO.sav CEL.sav --dodaj-druzyne [--miejsce N]       dodaj drużynę przygotowaną w ustawieniach
                                                           teamshare ZRODLA do miejsca N (1-10) w CELU
                                                           (bez --miejsce: pierwsze wolne)
    PLIK.sav --lista                                       pokaż 10 miejsc teamshare w pliku
    PLIK.sav --usun-druzyne N [-o WYJSCIE.sav]             usuń drużynę z miejsca N
    PLIK.sav --napraw [-o WYJSCIE.sav]                     popraw nagłówek bloku i sumy kontrolne
    ZRODLO.sav CEL.sav --info                              tylko podgląd

Układ zapisu (64 KB):
    0x00040  CRC32 nagłówka  = crc32(plik[0x44:0xC0])
    0x00044  CRC32 danych    = crc32(plik[0xC0:0x7E04])
    0x0008C  CRC32 teamshare = crc32(plik[0x7E04:0x7E04+0x5210])
    0x07E04  blok teamshare (0x5210 B; 0xFF gdy pusty) =
             8 B nagłówka (stałe 1, 1) + 10 miejsc po 2100 B
    miejsce: 0x000 znacznik, 0x016 id profilu nadawcy, 0x01C nazwa gracza, 0x04C komentarz,
             0x0D4 do 16 rekordów zawodników (po 0x70 B), 0x7D4 nazwa drużyny,
             0x7F4 dane drużyny, 0x814 lista id zawodników
Zawodnicy ustawieni w teamshare: w tabeli zawodników (dane główne, od 0x124C, po 0x70 B)
bajt +0x65 rekordu = numer miejsca w teamshare (1-15), 0 = nie wybrany.
"""

import argparse
import shutil
import struct
import sys
import zlib

LANG = "pl"


def set_lang(code: str) -> None:
    """Ustawia język tekstów: "pl" albo "en" / sets the language of the texts."""
    global LANG
    LANG = code if code in ("pl", "en") else "pl"


def L(pl: str, en: str) -> str:
    """Zwraca tekst w wybranym języku / returns the text in the selected language."""
    return en if LANG == "en" else pl
from pathlib import Path

SAVE_SIZE = 0x10000

OFF_HDR_CRC = 0x40
OFF_DATA_CRC = 0x44
OFF_TS_CRC = 0x8C
OFF_TS_SIZE = 0x90
HDR_CRC_START, HDR_CRC_END = 0x44, 0xC0
DATA_CRC_START = 0xC0
TS_START = 0x7E04
TS_DEFAULT_SIZE = 0x5210

# --- blok teamshare: 8 B nagłówka + 10 miejsc ---
BLOCK_HDR = 8
SLOT_SIZE = 2100
SLOT_COUNT = 10
assert BLOCK_HDR + SLOT_SIZE * SLOT_COUNT == TS_DEFAULT_SIZE

# offsety wewnątrz miejsca
S_USED = 0x00
S_FLAG = 0x12
S_ID = 0x16
S_NAME1 = 0x1C
S_NAME2 = 0x34
S_COMMENT = 0x4C
S_PREFIX = 0xCC
S_PLAYERS = 0xD4
S_TEAM_NAME = 0x7D4
S_TEAM_REC = 0x7F4
S_TEAM_LIST = 0x814
REC_SIZE = 0x70
MAX_PLAYERS = 16

# --- dane główne zapisu ---
HDR_PLAYER_NAME_OFF = 0x5C
MAIN_REC_BASE = 0x124C          # rekord zawodnika zaczyna się 4 B przed jego id
MAIN_REC_SLOTS = 51
MAIN_TEAM_NAME_OFF = 0x3E0C
MAIN_TEAM_REC_OFF = 0x3E2C
MAIN_PROFILE_ID_OFF = 0x54A
MAIN_COMMENT_OFF = 0x550
REC_POS = 0x57                  # numer miejsca w składzie / w paczce
REC_SLOT = 0x65                 # numer miejsca w teamshare (0 = nie wybrany)


class TransferError(Exception):
    """Błąd, który należy pokazać użytkownikowi."""


# ----------------------------------------------------------------- rdzeń ---

def crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def cstr(data: bytes, off: int, maxlen: int) -> str:
    raw = bytes(data[off:off + maxlen]).split(b"\x00")[0]
    return raw.decode("ascii", errors="replace")


def load(path) -> bytearray:
    path = Path(path)
    try:
        data = bytearray(path.read_bytes())
    except OSError as e:
        raise TransferError(L(f"Nie można odczytać pliku {path}:\n{e}", f"Cannot read file {path}:\n{e}"))
    if len(data) != SAVE_SIZE:
        raise TransferError(L(f"{path.name} ma {len(data)} bajtów, oczekiwano {SAVE_SIZE} (64 KB).", f"{path.name} is {len(data)} bytes, expected {SAVE_SIZE} (64 KB)."))
    if b"INAZUMA_11" not in data[:0x40]:
        raise TransferError(L(f"{path.name} nie wygląda na zapis Inazuma Eleven 1 (brak nagłówka INAZUMA_11).", f"{path.name} does not look like an Inazuma Eleven 1 save (INAZUMA_11 header missing)."))
    if u32(data, OFF_TS_SIZE) != TS_DEFAULT_SIZE:
        raise TransferError(L(f"{path.name}: nieobsługiwany rozmiar bloku teamshare.", f"{path.name}: unsupported teamshare block size."))
    return data


def block(data) -> bytes:
    return bytes(data[TS_START:TS_START + TS_DEFAULT_SIZE])


def has_teamshare(data) -> bool:
    return block(data).count(0xFF) != TS_DEFAULT_SIZE


def check_crcs(data) -> dict:
    return {
        "nagłówek": crc32(data[HDR_CRC_START:HDR_CRC_END]) == u32(data, OFF_HDR_CRC),
        "dane główne": crc32(data[DATA_CRC_START:TS_START]) == u32(data, OFF_DATA_CRC),
        "teamshare": crc32(block(data)) == u32(data, OFF_TS_CRC),
    }


def fix_crcs(data: bytearray) -> None:
    struct.pack_into("<I", data, OFF_TS_CRC, crc32(block(data)))
    struct.pack_into("<I", data, OFF_DATA_CRC, crc32(data[DATA_CRC_START:TS_START]))
    struct.pack_into("<I", data, OFF_HDR_CRC, crc32(data[HDR_CRC_START:HDR_CRC_END]))


def default_output(dst_path) -> Path:
    p = Path(dst_path)
    return p.with_name(p.stem + "_teamshare.sav")


# ------------------------------------------------- miejsca teamshare (10) ---

def slot_range(i: int):
    s = TS_START + BLOCK_HDR + SLOT_SIZE * i
    return s, s + SLOT_SIZE


def read_slots(data) -> list:
    """Lista 10 elementów: None (puste miejsce) albo słownik z opisem drużyny."""
    if not has_teamshare(data):
        return [None] * SLOT_COUNT
    out = []
    for i in range(SLOT_COUNT):
        s, e = slot_range(i)
        sl = bytes(data[s:e])
        if sl[S_USED] == 0:
            out.append(None)
            continue
        n = sum(1 for k in range(MAX_PLAYERS) if sl[S_TEAM_LIST + 2 * k:S_TEAM_LIST + 2 * k + 2] != b"\0\0")
        out.append({"team": cstr(sl, S_TEAM_NAME, 0x20), "author": cstr(sl, S_NAME1, 0x10),
                    "comment": cstr(sl, S_COMMENT, 0x20), "players": n})
    return out


def slot_title(i: int, info) -> str:
    if info is None:
        return L(f"{i + 1:>2}.  (puste)", f"{i + 1:>2}.  (empty)")
    return L(f"{i + 1:>2}.  {info['team']}   –   autor: {info['author']}, "
             f"komentarz: „{info['comment']}”, zawodników: {info['players']}",
             f"{i + 1:>2}.  {info['team']}   –   author: {info['author']}, "
             f"comment: “{info['comment']}”, players: {info['players']}")


def first_free(data):
    for i, info in enumerate(read_slots(data)):
        if info is None:
            return i
    return None


def _get_slots_raw(data) -> list:
    if not has_teamshare(data):
        return [None] * SLOT_COUNT
    raws = []
    for i in range(SLOT_COUNT):
        s, e = slot_range(i)
        sl = bytes(data[s:e])
        raws.append(sl if sl[S_USED] else None)
    return raws


def _write_slots(data: bytearray, raws: list) -> None:
    """Zapisuje listę miejsc (None = puste) do bloku, ustawia nagłówek i sumy kontrolne."""
    raws = [r for r in raws if r is not None]          # lista zawsze „zbita” od góry, bez dziur
    count = len(raws)
    raws = raws + [None] * (SLOT_COUNT - count)
    if count == 0:
        data[TS_START:TS_START + TS_DEFAULT_SIZE] = b"\xff" * TS_DEFAULT_SIZE
    else:
        blk = bytearray(TS_DEFAULT_SIZE)
        struct.pack_into("<II", blk, 0, 1, 1)       # nagłówek bloku jest stały (1, 1), niezależnie od liczby drużyn
        for i, r in enumerate(raws):
            if r is not None:
                blk[BLOCK_HDR + SLOT_SIZE * i:BLOCK_HDR + SLOT_SIZE * (i + 1)] = r
        data[TS_START:TS_START + TS_DEFAULT_SIZE] = blk
    fix_crcs(data)


def put_slot(data: bytearray, index: int, slot: bytes) -> int:
    """Wstawia drużynę na miejsce `index`. Jeśli to miejsce jest puste, drużyna trafia na pierwsze
    wolne (lista jest zawsze ciągła). Zwraca numer miejsca (0-9), na którym faktycznie wylądowała."""
    raws = _get_slots_raw(data)
    if raws[index] is None:
        index = raws.index(None)
    raws[index] = bytes(slot)
    _write_slots(data, raws)
    return index


def remove_slot(data: bytearray, index: int) -> None:
    """Usuwa drużynę; kolejne przesuwają się w górę (bez dziur na liście)."""
    raws = _get_slots_raw(data)
    del raws[index]
    raws.append(None)
    _write_slots(data, raws)


# ------------------------------------------- drużyna z ustawień teamshare ---

def roster(data) -> list:
    out = []
    for j in range(MAIN_REC_SLOTS):
        rec = bytes(data[MAIN_REC_BASE + REC_SIZE * j:MAIN_REC_BASE + REC_SIZE * (j + 1)])
        if any(rec[4:]):
            out.append(rec)
    return out


def prepared_team(data) -> list:
    """Zawodnicy ustawieni w teamshare (bajt REC_SLOT != 0), w kolejności miejsc."""
    chosen = sorted((r for r in roster(data) if r[REC_SLOT] != 0), key=lambda r: r[REC_SLOT])
    return chosen[:MAX_PLAYERS]


def describe_prepared(data) -> str:
    team = cstr(data, MAIN_TEAM_NAME_OFF, 0x20)
    comment = cstr(data, MAIN_COMMENT_OFF, 0x20)
    sel = prepared_team(data)
    if not sel:
        return L(f"   drużyna w grze: {team!r}, komentarz: {comment!r}\n"
                 f"   UWAGA: nikt nie jest ustawiony w teamshare - nie ma czego dodać",
                 f"   team in the game: {team!r}, comment: {comment!r}\n"
                 f"   WARNING: no player is set in teamshare - nothing to add")
    ids = ", ".join(f"{int.from_bytes(r[4:6], 'little'):#x}" for r in sel)
    return L(f"   drużyna przygotowana w teamshare: {team!r}, komentarz: {comment!r}\n"
             f"   zawodników: {len(sel)}  (id wg miejsc 1..: {ids})",
             f"   team prepared in teamshare: {team!r}, comment: {comment!r}\n"
             f"   players: {len(sel)}  (ids by slot 1..: {ids})")


def build_slot(m) -> bytes:
    """Buduje jedno miejsce teamshare (2100 B) z drużyny przygotowanej w ustawieniach teamshare zapisu m."""
    recs = prepared_team(m)
    if not recs:
        raise TransferError(L("W zapisie źródłowym nie ma drużyny przygotowanej w ustawieniach teamshare "
                              "(żaden zawodnik nie jest ustawiony).",
                              "The source save has no team prepared in the teamshare settings "
                              "(no player is set)."))
    sl = bytearray(SLOT_SIZE)
    sl[S_USED] = 1
    sl[1:3] = m[0x54:0x56]
    sl[3] = m[0x85]
    sl[4:8] = m[0x50:0x54]
    sl[8:12] = m[0x58:0x5C]
    sl[S_FLAG] = 0x0F
    sl[S_ID:S_ID + 6] = m[MAIN_PROFILE_ID_OFF:MAIN_PROFILE_ID_OFF + 6]
    player = bytes(m[HDR_PLAYER_NAME_OFF:HDR_PLAYER_NAME_OFF + 0x10]).split(b"\x00")[0]
    sl[S_NAME1:S_NAME1 + len(player)] = player
    sl[S_NAME2:S_NAME2 + len(player)] = player
    comment = bytes(m[MAIN_COMMENT_OFF:MAIN_COMMENT_OFF + 0x20]).split(b"\x00")[0]
    sl[S_COMMENT:S_COMMENT + len(comment)] = comment
    sl[S_PREFIX:S_PREFIX + 4] = bytes.fromhex("02006b01")

    ids = []
    for k, r in enumerate(recs):
        r = bytearray(r)
        r[REC_POS] = k + 1
        r[REC_SLOT] = k + 1
        sl[S_PLAYERS + REC_SIZE * k:S_PLAYERS + REC_SIZE * (k + 1)] = r
        ids.append(bytes(r[4:6]))
    sl[S_TEAM_NAME:S_TEAM_NAME + 0x20] = m[MAIN_TEAM_NAME_OFF:MAIN_TEAM_NAME_OFF + 0x20]
    sl[S_TEAM_REC:S_TEAM_REC + 0x20] = m[MAIN_TEAM_REC_OFF:MAIN_TEAM_REC_OFF + 0x20]
    sl[S_TEAM_LIST:S_TEAM_LIST + 2 * len(ids)] = b"".join(ids)
    return bytes(sl)


# ------------------------------------------------------------- opisy / zapis ---

def describe(title: str, data) -> str:
    lines = [f"== {title}", L(f"   gracz (nagłówek): {cstr(data, HDR_PLAYER_NAME_OFF, 0x10)!r}", f"   player (header): {cstr(data, HDR_PLAYER_NAME_OFF, 0x10)!r}")]
    slots = read_slots(data)
    used = [s for s in slots if s]
    if used:
        lines.append(L(f"   teamshare: {len(used)} z {SLOT_COUNT} miejsc zajętych", f"   teamshare: {len(used)} of {SLOT_COUNT} slots used"))
        for i, s in enumerate(slots):
            if s:
                lines.append("   " + slot_title(i, s))
    else:
        lines.append(L("   teamshare: pusty", "   teamshare: empty"))
    ok = check_crcs(data)
    names = {"nagłówek": L("nagłówek", "header"), "dane główne": L("dane główne", "main data"), "teamshare": "teamshare"}
    lines.append(L("   sumy kontrolne: ", "   checksums: ") +
                 ", ".join(f"{names.get(k, k)}={'OK' if v else L('BŁĄD', 'ERROR')}" for k, v in ok.items()))
    return "\n".join(lines)


def save_data(data: bytearray, dst_path, out_path=None, log=print) -> Path:
    """Zapisuje dane do pliku wynikowego (przy nadpisywaniu celu robi kopię .bak)."""
    dst_path = Path(dst_path)
    out_path = Path(out_path) if out_path else default_output(dst_path)
    if out_path.exists() and out_path.resolve() == dst_path.resolve():
        backup = dst_path.with_suffix(".sav.bak")
        shutil.copy2(dst_path, backup)
        log(L(f"Kopia zapasowa celu: {backup}", f"Backup of the target: {backup}"))
    if has_teamshare(data):
        _write_slots(data, _get_slots_raw(data))      # normalizuje nagłówek bloku (1, 1) i układ miejsc
    fix_crcs(data)
    try:
        out_path.write_bytes(data)
    except OSError as e:
        raise TransferError(L(f"Nie można zapisać pliku {out_path}:\n{e}", f"Cannot write file {out_path}:\n{e}"))
    log(L(f"\nZapisano: {out_path}", f"\nSaved: {out_path}"))
    log(describe(L(f"WYNIK:  {out_path.name}", f"RESULT: {out_path.name}"), data))
    return out_path


def transfer(src_path, dst_path, out_path=None, log=print) -> Path:
    """Kopiuje cały blok teamshare (wszystkie 10 miejsc) ze źródła do celu."""
    src_path, dst_path = Path(src_path), Path(dst_path)
    src, dst = load(src_path), load(dst_path)
    log(describe(L(f"ŹRÓDŁO: {src_path.name}", f"SOURCE: {src_path.name}"), src))
    log(describe(L(f"CEL:    {dst_path.name}", f"TARGET: {dst_path.name}"), dst))

    if not has_teamshare(src):
        raise TransferError(L("Źródło nie zawiera danych teamshare (blok pusty).",
                              "The source contains no teamshare data (empty block)."))
    if not check_crcs(src)["teamshare"]:
        log(L("Uwaga: suma kontrolna teamshare w źródle była błędna - zostanie przeliczona.",
              "Note: the teamshare checksum in the source was wrong - it will be recalculated."))
    if has_teamshare(dst):
        log(L("Uwaga: istniejący teamshare w celu zostanie zastąpiony.",
              "Note: the existing teamshare in the target will be replaced."))
    dst[TS_START:TS_START + TS_DEFAULT_SIZE] = src[TS_START:TS_START + TS_DEFAULT_SIZE]
    return save_data(dst, dst_path, out_path, log)


def add_team(src_path, dst_path, out_path=None, slot=None, log=print) -> Path:
    """Dodaje drużynę przygotowaną w ustawieniach teamshare źródła do miejsca `slot` (0-9) celu."""
    src_path, dst_path = Path(src_path), Path(dst_path)
    src, dst = load(src_path), load(dst_path)
    log(describe(L(f"ŹRÓDŁO: {src_path.name}", f"SOURCE: {src_path.name}"), src))
    log(describe_prepared(src))
    log(describe(L(f"CEL:    {dst_path.name}", f"TARGET: {dst_path.name}"), dst))

    new_slot = build_slot(src)
    if slot is None:
        slot = first_free(dst)
        if slot is None:
            raise TransferError(L("Wszystkie 10 miejsc teamshare w celu jest zajętych - wskaż miejsce do zamiany.", "All 10 teamshare slots in the target are taken - choose a slot to replace."))
    slot = put_slot(dst, slot, new_slot)
    log(L(f"Drużyna wstawiona na miejsce {slot + 1}.", f"Team placed in slot {slot + 1}."))
    return save_data(dst, dst_path, out_path, log)


# ------------------------------------------------------------------- GUI ---

def run_gui() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, ttk

    root = tk.Tk()
    root.title("Inazuma Eleven 1 (NDS) – teamshare")
    root.geometry("820x730")
    root.minsize(700, 600)

    src_var, dst_var, out_var = tk.StringVar(), tk.StringVar(), tk.StringVar()
    mode_var = tk.StringVar(value="copy")
    lang_var = tk.StringVar(value=LANG)
    src_label = tk.StringVar()
    btn_text = tk.StringVar()
    out_manual = {"set": False}
    state = {"work": None}     # kopia celu w pamięci (tryb „10 miejsc”) / in-memory copy of the target
    h = {}                     # funkcje bieżącego okna (budowanego od nowa przy zmianie języka)

    src_var.trace_add("write", lambda *_: h["refresh_info"]())
    dst_var.trace_add("write", lambda *_: h["on_dst_change"]())

    def build() -> None:
        # ---- wybór języka / language selector ----
        top = ttk.Frame(root, padding=(12, 8, 12, 0))
        top.pack(fill="x")
        ttk.Label(top, text="Język / Language:").pack(side="left")
        ttk.Radiobutton(top, text="Polski", value="pl", variable=lang_var,
                        command=lambda: root.after_idle(switch_lang)).pack(side="left", padx=(8, 0))
        ttk.Radiobutton(top, text="English", value="en", variable=lang_var,
                        command=lambda: root.after_idle(switch_lang)).pack(side="left", padx=(8, 0))

        frm = ttk.Frame(root, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)
        frm.rowconfigure(6, weight=1)

        log_box = scrolledtext.ScrolledText(frm, height=10, state="disabled", font=("Consolas", 9))
        log_box.grid(row=6, column=0, columnspan=3, sticky="nsew", pady=(8, 0))

        def log(text: str, clear: bool = False) -> None:
            log_box.configure(state="normal")
            if clear:
                log_box.delete("1.0", "end")
            log_box.insert("end", text + "\n")
            log_box.see("end")
            log_box.configure(state="disabled")

        # ---- lista 10 miejsc / list of 10 slots ----
        slots_frame = ttk.LabelFrame(
            frm, text=L("Miejsca w twoim pliku zapisu teamshare(maks. 10 drużyn)",
                        "Teamshare slots in the target file (max. 10 teams)"), padding=8)
        slots_frame.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        slots_frame.columnconfigure(0, weight=1)
        lb = tk.Listbox(slots_frame, height=10, exportselection=False, font=("Consolas", 9), activestyle="none")
        lb.grid(row=0, column=0, rowspan=4, sticky="ew")
        btns = ttk.Frame(slots_frame)
        btns.grid(row=0, column=1, rowspan=4, padx=(8, 0), sticky="n")

        def selected_index():
            sel = lb.curselection()
            return sel[0] if sel else None

        def refresh_slots(select=None):
            lb.delete(0, "end")
            work = state["work"]
            infos = read_slots(work) if work is not None else [None] * SLOT_COUNT
            for i, info in enumerate(infos):
                lb.insert("end", slot_title(i, info))
            if select is not None:
                lb.selection_set(select)
                lb.see(select)

        def load_source():
            p = src_var.get().strip()
            if not p:
                raise TransferError(L("Wybierz plik źródłowy.", "Choose the source file."))
            return load(p)

        def need_work():
            if state["work"] is None:
                raise TransferError(L("Wybierz plik docelowy.", "Choose the target file."))
            return state["work"]

        def act_add():
            try:
                work = need_work()
                slot = build_slot(load_source())
                idx = first_free(work)
                if idx is None:
                    raise TransferError(L("Wszystkie 10 miejsc jest zajętych. Zaznacz miejsce i użyj „Zamień”.",
                                          "All 10 slots are taken. Select a slot and use “Replace”."))
                idx = put_slot(work, idx, slot)
                log(L(f"Dodano drużynę na miejsce {idx + 1}. Kliknij „Zapisz plik wynikowy”, by zapisać zmiany.",
                      f"Team added to slot {idx + 1}. Click “Save output file” to save the changes."))
                refresh_slots(idx)
            except TransferError as e:
                messagebox.showwarning(L("Dodaj", "Add"), str(e))

        def act_replace():
            try:
                work = need_work()
                idx = selected_index()
                if idx is None:
                    raise TransferError(L("Zaznacz na liście miejsce, które chcesz zamienić.",
                                          "Select the slot you want to replace in the list."))
                slot = build_slot(load_source())
                info = read_slots(work)[idx]
                if info and not messagebox.askyesno(
                        L("Zamień", "Replace"),
                        L(f"Zastąpić drużynę „{info['team']}” (miejsce {idx + 1})?",
                          f"Replace team “{info['team']}” (slot {idx + 1})?")):
                    return
                idx = put_slot(work, idx, slot)
                log(L(f"Zamieniono drużynę na miejscu {idx + 1}. Kliknij „Zapisz plik wynikowy”, by zapisać zmiany.",
                      f"Team in slot {idx + 1} replaced. Click “Save output file” to save the changes."))
                refresh_slots(idx)
            except TransferError as e:
                messagebox.showwarning(L("Zamień", "Replace"), str(e))

        def act_remove():
            try:
                work = need_work()
                idx = selected_index()
                if idx is None:
                    raise TransferError(L("Zaznacz na liście drużynę do usunięcia.",
                                          "Select the team to remove in the list."))
                info = read_slots(work)[idx]
                if info is None:
                    raise TransferError(L("To miejsce jest już puste.", "This slot is already empty."))
                if not messagebox.askyesno(
                        L("Usuń", "Remove"),
                        L(f"Usunąć drużynę „{info['team']}” (miejsce {idx + 1})?\n"
                          "Kolejne drużyny przesuną się o jedno miejsce w górę.",
                          f"Remove team “{info['team']}” (slot {idx + 1})?\n"
                          "The following teams will move up by one slot.")):
                    return
                remove_slot(work, idx)
                log(L(f"Usunięto drużynę z miejsca {idx + 1}. Kliknij „Zapisz plik wynikowy”, by zapisać zmiany.",
                      f"Team removed from slot {idx + 1}. Click “Save output file” to save the changes."))
                refresh_slots(min(idx, SLOT_COUNT - 1))
            except TransferError as e:
                messagebox.showwarning(L("Usuń", "Remove"), str(e))

        ttk.Button(btns, text=L("Dodaj drużynę z z zapisu rywala\n(doda do pierwszego wolnego miejsca)",
                                "Add team from source\n(first free slot)"),
                   command=act_add).pack(fill="x", pady=2)
        ttk.Button(btns, text=L("Zamień zaznaczoną drużynę\nna drużynę z zapisu rywala",
                                "Replace selected\nwith team from source"),
                   command=act_replace).pack(fill="x", pady=2)
        ttk.Button(btns, text=L("Usuń zaznaczoną", "Remove selected"), command=act_remove).pack(fill="x", pady=2)

        # ---- informacje / information ----
        def refresh_info(*_):
            parts = []
            p = src_var.get().strip()
            if p:
                try:
                    data = load(p)
                    text = describe(L(f"ŹRÓDŁO: {Path(p).name}", f"SOURCE: {Path(p).name}"), data)
                    if mode_var.get() == "add":
                        text += "\n" + describe_prepared(data)
                    parts.append(text)
                except TransferError as e:
                    parts.append(L(f"== ŹRÓDŁO: BŁĄD\n   {e}", f"== SOURCE: ERROR\n   {e}"))
            if mode_var.get() == "copy":
                p = dst_var.get().strip()
                if p:
                    try:
                        parts.append(describe(L(f"CEL: {Path(p).name}", f"TARGET: {Path(p).name}"), load(p)))
                    except TransferError as e:
                        parts.append(L(f"== CEL: BŁĄD\n   {e}", f"== TARGET: ERROR\n   {e}"))
            log("\n\n".join(parts) if parts else
                L("Wybierz plik źródłowy i docelowy.", "Choose the source and target files."), clear=True)

        def reload_work():
            state["work"] = None
            p = dst_var.get().strip()
            if p and mode_var.get() == "add":
                try:
                    state["work"] = load(p)
                except TransferError as e:
                    log(L(f"BŁĄD (cel): {e}", f"ERROR (target): {e}"), clear=True)
            refresh_slots()

        def sav_types():
            return [(L("Zapisy NDS", "NDS saves"), "*.sav"), (L("Wszystkie pliki", "All files"), "*.*")]

        def pick(var, title):
            path = filedialog.askopenfilename(title=title, filetypes=sav_types())
            if path:
                var.set(path)

        def pick_out():
            initial = Path(out_var.get()).name if out_var.get() else "wynik.sav"
            path = filedialog.asksaveasfilename(
                title=L("Zapisz wynik jako", "Save result as"), defaultextension=".sav", initialfile=initial,
                filetypes=sav_types())
            if path:
                out_manual["set"] = True
                out_var.set(path)

        def on_dst_change(*_):
            if not out_manual["set"] and dst_var.get().strip():
                out_var.set(str(default_output(dst_var.get().strip())))
            reload_work()
            refresh_info()

        def apply_mode_labels():
            if mode_var.get() == "add":
                src_label.set(L("Plik przeciwnika (zapis z drużyną\nprzygotowaną w teamshare):",
                                "Source (save with the team\nprepared in teamshare):"))
                btn_text.set(L("Zapisz zmiany", "Save output file"))
                slots_frame.grid()
            else:
                src_label.set(L("Plik zapisu z drużyną którą chcesz dodać:", "Source (with teamshare):"))
                btn_text.set(L("Wykonaj", "Run"))
                slots_frame.grid_remove()

        def on_mode(*_):
            apply_mode_labels()
            reload_work()
            refresh_info()

        # ---- pola plików / file fields ----
        ttk.Label(frm, textvariable=src_label).grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=src_var).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(frm, text=L("Wybierz…", "Browse…"),
                   command=lambda: pick(src_var, L("Plik źródłowy", "Source file"))).grid(row=0, column=2)

        ttk.Label(frm, text=L("Twój plik zapisu:", "Target:")).grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=dst_var).grid(row=1, column=1, sticky="ew", padx=6)
        ttk.Button(frm, text=L("Wybierz…", "Browse…"),
                   command=lambda: pick(dst_var, L("Plik docelowy", "Target file"))).grid(row=1, column=2)

        ttk.Label(frm, text=L("Wybierz gdzie zapisać plik z dodaną drużyną:", "Output file:")).grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=out_var).grid(row=2, column=1, sticky="ew", padx=6)
        ttk.Button(frm, text=L("Zapisz jako…", "Save as…"), command=pick_out).grid(row=2, column=2)

        modes = ttk.Frame(frm)
        modes.grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Radiobutton(modes, text=L("Skopiuj drużyny teamshare z zapisu rywala (jeśli ma na przykład 10 zajętych slotów innych drużyn a ty żadnego możesz je skopiować na swój zapis)",
                                      "Copy the whole teamshare from source to target"),
                        value="copy", variable=mode_var, command=on_mode).pack(anchor="w")
        ttk.Radiobutton(modes, text=L("Dodaj / zamień / usuń drużyny w twoim pliku zapisu teamshare (10 drużyn)",
                                      "Add / replace / remove teams in the target's teamshare (10 slots)"),
                        value="add", variable=mode_var, command=on_mode).pack(anchor="w")

        def do_main():
            src, dst, out = src_var.get().strip(), dst_var.get().strip(), out_var.get().strip()
            if not dst or (mode_var.get() == "copy" and not src):
                messagebox.showwarning(L("Brak plików", "Missing files"),
                                       L("Wybierz plik zapisu przeciwnika i twój.", "Choose the source and target files."))
                return
            if not out:
                out = str(default_output(dst))
                out_var.set(out)
            if mode_var.get() == "copy" and Path(src).resolve() == Path(dst).resolve():
                messagebox.showwarning(L("Te same pliki", "Same files"),
                                       L("Twój zapis i przeciwnika to ten sam plik.", "Source and target are the same file."))
                return
            if Path(out).exists():
                same = Path(out).resolve() == Path(dst).resolve()
                msg = (L("Plik z dodaną drużyną to plik docelowy – zostanie nadpisany (powstanie kopia .bak). Kontynuować?",
                         "The output file is the target file – it will be overwritten (a .bak backup will be created). Continue?")
                       if same else L(f"Plik {Path(out).name} już istnieje. Nadpisać?",
                                      f"File {Path(out).name} already exists. Overwrite?"))
                if not messagebox.askyesno(L("Nadpisać?", "Overwrite?"), msg):
                    return
            try:
                if mode_var.get() == "copy":
                    log_box.configure(state="normal"); log_box.delete("1.0", "end"); log_box.configure(state="disabled")
                    result = transfer(src, dst, out, log=log)
                else:
                    if state["work"] is None:
                        raise TransferError(L("Wybierz plik docelowy.", "Choose the target file."))
                    result = save_data(state["work"], dst, out, log=log)
            except TransferError as e:
                log(L(f"BŁĄD: {e}", f"ERROR: {e}"))
                messagebox.showerror(L("Błąd", "Error"), str(e))
                return
            messagebox.showinfo(L("Gotowe", "Done"), L(f"Zapisano:\n{result}", f"Saved:\n{result}"))

        ttk.Button(frm, textvariable=btn_text, command=do_main).grid(
            row=5, column=0, columnspan=3, pady=10, ipadx=20, ipady=4)

        h.update(refresh_info=refresh_info, on_dst_change=on_dst_change)
        apply_mode_labels()
        refresh_slots()
        refresh_info()

    def switch_lang() -> None:
        set_lang(lang_var.get())
        for w in root.winfo_children():
            w.destroy()
        build()

    build()
    root.mainloop()


# ------------------------------------------------------------------- CLI ---

def main() -> None:
    # --lang pl|en jest wyłuskiwane najpierw, żeby pomoc i komunikaty były w wybranym języku
    argv, lang, i = [], None, 1
    while i < len(sys.argv):
        a = sys.argv[i]
        if a == "--lang" and i + 1 < len(sys.argv):
            lang = sys.argv[i + 1]
            i += 2
        elif a.startswith("--lang="):
            lang = a.split("=", 1)[1]
            i += 1
        else:
            argv.append(a)
            i += 1
    if lang is not None:
        if lang not in ("pl", "en"):
            sys.exit("--lang: pl | en")
        set_lang(lang)

    if not argv:
        try:
            run_gui()
        except ImportError:
            sys.exit(L("Brak modułu tkinter. Zainstaluj go (Linux: sudo apt install python3-tk) "
                       "lub użyj trybu wiersza poleceń: python ie_teamshare_transfer.py -h",
                       "The tkinter module is missing. Install it (Linux: sudo apt install python3-tk) "
                       "or use the command-line mode: python ie_teamshare_transfer.py -h"))
        return

    ap = argparse.ArgumentParser(description=L("Teamshare w zapisach Inazuma Eleven 1 (NDS).",
                                               "Teamshare in Inazuma Eleven 1 (NDS) saves."))
    ap.add_argument("zrodlo", nargs="?", type=Path, metavar=L("ZRODLO", "SOURCE"),
                    help=L("zapis źródłowy (dla --lista i --usun-druzyne: edytowany plik)",
                           "source save (for --list and --remove-team: the file to edit)"))
    ap.add_argument("cel", nargs="?", type=Path, metavar=L("Twój zapis", "TARGET"),
                    help=L("zapis docelowy", "target save"))
    ap.add_argument("-o", "--output", type=Path,
                    help=L("plik wynikowy (domyślnie: <cel>_teamshare.sav)",
                           "output file (default: <target>_teamshare.sav)"))
    ap.add_argument("--info", action="store_true",
                    help=L("tylko pokaż informacje o plikach", "only show information about the files"))
    ap.add_argument("--gui", action="store_true", help=L("uruchom okno graficzne", "open the graphical window"))
    ap.add_argument("--lang", choices=("pl", "en"),
                    help=L("język tekstów: pl lub en", "language of the texts: pl or en"))
    ap.add_argument("--dodaj-druzyne", "--add-team", dest="dodaj_druzyne", action="store_true",
                    help=L("dodaj drużynę przygotowaną w ustawieniach teamshare źródła do celu",
                           "add the team prepared in the source's teamshare settings to the target"))
    ap.add_argument("--miejsce", "--slot", dest="miejsce", type=int, choices=range(1, SLOT_COUNT + 1),
                    metavar="1-10",
                    help=L("miejsce w teamshare celu (domyślnie pierwsze wolne)",
                           "slot in the target's teamshare (default: first free)"))
    ap.add_argument("--lista", "--list", dest="lista", action="store_true",
                    help=L("pokaż 10 miejsc teamshare w pliku", "show the 10 teamshare slots in the file"))
    ap.add_argument("--napraw", "--repair", dest="napraw", action="store_true",
                    help=L("napraw plik: ustaw poprawny nagłówek bloku teamshare, zbij listę i przelicz sumy kontrolne",
                           "repair the file: set the correct teamshare block header, pack the list and recalculate checksums"))
    ap.add_argument("--usun-druzyne", "--remove-team", dest="usun_druzyne", type=int,
                    choices=range(1, SLOT_COUNT + 1), metavar="1-10",
                    help=L("usuń drużynę z podanego miejsca pliku", "remove the team from the given slot of the file"))
    ap.add_argument("--force", action="store_true", help=argparse.SUPPRESS)   # zachowane dla zgodności
    args = ap.parse_args(argv)

    if args.gui:
        run_gui()
        return
    if not args.zrodlo:
        ap.error(L("podaj plik (albo uruchom bez argumentów, by otworzyć GUI)",
                   "give a file (or run without arguments to open the GUI)"))

    try:
        if args.lista:
            print(describe(L(f"PLIK: {args.zrodlo.name}", f"FILE: {args.zrodlo.name}"), load(args.zrodlo)))
            print("\n".join(slot_title(i, s) for i, s in enumerate(read_slots(load(args.zrodlo)))))
        elif args.napraw:
            data = load(args.zrodlo)
            save_data(data, args.zrodlo, args.output or args.zrodlo.with_name(args.zrodlo.stem + "_naprawiony.sav"))
        elif args.usun_druzyne:
            data = load(args.zrodlo)
            idx = args.usun_druzyne - 1
            info = read_slots(data)[idx]
            if info is None:
                raise TransferError(L(f"Miejsce {args.usun_druzyne} jest już puste.",
                                      f"Slot {args.usun_druzyne} is already empty."))
            remove_slot(data, idx)
            print(L(f"Usunięto drużynę „{info['team']}” z miejsca {args.usun_druzyne}.",
                    f"Team “{info['team']}” removed from slot {args.usun_druzyne}."))
            save_data(data, args.zrodlo, args.output)
        else:
            if not args.cel:
                ap.error(L("podaj plik źródłowy i docelowy", "give the source and target files"))
            if args.info:
                print(describe(L(f"ŹRÓDŁO: {args.zrodlo.name}", f"SOURCE: {args.zrodlo.name}"), load(args.zrodlo)))
                print(describe(L(f"CEL:    {args.cel.name}", f"TARGET: {args.cel.name}"), load(args.cel)))
            elif args.dodaj_druzyne:
                add_team(args.zrodlo, args.cel, args.output,
                         slot=None if args.miejsce is None else args.miejsce - 1)
            else:
                transfer(args.zrodlo, args.cel, args.output)
    except TransferError as e:
        sys.exit(L(f"BŁĄD: {e}", f"ERROR: {e}"))


if __name__ == "__main__":
    main()
