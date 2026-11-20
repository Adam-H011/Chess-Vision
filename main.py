import os
import platform
import threading
import time
import urllib.request
import tkinter as tk
from tkinter import messagebox
import chess
import chess.engine
import cv2
import numpy as np
from PIL import Image, ImageTk

# --- KONFIGURATION ---
if platform.system() == "Windows":
    STOCKFISH_PATH = "stockfish.exe"
else:
    STOCKFISH_PATH = "/usr/bin/stockfish"

SPRITE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sprites_neo")
CHESSCOM_CDN = "https://images.chesscomfiles.com/chess-themes/pieces/neo/150"

PIECE_CODES = {
    (chess.WHITE, chess.PAWN): "wp",
    (chess.WHITE, chess.KNIGHT): "wn",
    (chess.WHITE, chess.BISHOP): "wb",
    (chess.WHITE, chess.ROOK): "wr",
    (chess.WHITE, chess.QUEEN): "wq",
    (chess.WHITE, chess.KING): "wk",
    (chess.BLACK, chess.PAWN): "bp",
    (chess.BLACK, chess.KNIGHT): "bn",
    (chess.BLACK, chess.BISHOP): "bb",
    (chess.BLACK, chess.ROOK): "br",
    (chess.BLACK, chess.QUEEN): "bq",
    (chess.BLACK, chess.KING): "bk",
}

SOLID_SYMBOLS = {
    "p": "♟", "n": "♞", "b": "♝", "r": "♜", "q": "♛", "k": "♚"
}


class WaylandScreenGrabber:
    """Nutzt Gio.DBusProxy + PipeWire + GStreamer direkt."""
    def __init__(self):
        self.latest_frame = None
        self.lock = threading.Lock()
        self.running = False
        self.is_wayland = platform.system() == "Linux" and bool(os.environ.get("WAYLAND_DISPLAY"))

    def start_portal_stream(self, on_ready_callback, on_error_callback):
        if not self.is_wayland:
            on_ready_callback()
            return

        def _worker():
            try:
                import gi
                gi.require_version("Gst", "1.0")
                gi.require_version("Gio", "2.0")
                from gi.repository import Gio, GLib, Gst

                Gst.init(None)

                bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                sender = bus.get_unique_name().replace(".", "_").replace(":", "")
                ts = int(time.time() * 1000)
                session_token = f"chess_vision_{ts}"
                session_path = f"/org/freedesktop/portal/desktop/session/{sender}/{session_token}"

                screencast = Gio.DBusProxy.new_sync(
                    bus,
                    Gio.DBusProxyFlags.NONE,
                    None,
                    "org.freedesktop.portal.Desktop",
                    "/org/freedesktop/portal/desktop",
                    "org.freedesktop.portal.ScreenCast",
                    None,
                )

                loop = GLib.MainLoop()

                def call_portal_method(method_name, parameters, handle_token, response_cb):
                    req_path = f"/org/freedesktop/portal/desktop/request/{sender}/{handle_token}"
                    sub_id = [None]

                    def _on_signal(conn, sender_name, obj_path, iface_name, sig_name, params):
                        if sub_id[0] is not None:
                            bus.signal_unsubscribe(sub_id[0])
                        response_code, results = params.unpack()
                        response_cb(response_code, results)

                    sub_id[0] = bus.signal_subscribe(
                        "org.freedesktop.portal.Desktop",
                        "org.freedesktop.portal.Request",
                        "Response",
                        req_path,
                        None,
                        Gio.DBusSignalFlags.NO_MATCH_RULE,
                        _on_signal,
                    )
                    screencast.call_sync(method_name, parameters, Gio.DBusCallFlags.NONE, -1, None)

                def on_start_response(response, results):
                    if response != 0:
                        loop.quit()
                        on_error_callback("Bildschirmfreigabe abgebrochen.")
                        return

                    streams = results.get("streams", [])
                    if not streams:
                        loop.quit()
                        on_error_callback("Kein Stream ausgewählt.")
                        return

                    node_id = streams[0][0]
                    res_variant, fd_list = screencast.call_with_unix_fd_list_sync(
                        "OpenPipeWireRemote",
                        GLib.Variant("(oa{sv})", (session_path, {})),
                        Gio.DBusCallFlags.NONE,
                        -1,
                        None,
                        None,
                    )
                    fd_idx = res_variant.unpack()[0]
                    fd = fd_list.steal_fds()[fd_idx]

                    pipeline_str = (
                        f"pipewiresrc fd={fd} path={node_id} do-timestamp=true keepalive-time=1000 ! "
                        "videoconvert ! video/x-raw,format=RGB ! appsink name=sink emit-signals=true max-buffers=1 drop=true"
                    )
                    pipeline = Gst.parse_launch(pipeline_str)
                    appsink = pipeline.get_by_name("sink")

                    def on_new_sample(sink):
                        sample = sink.emit("pull-sample")
                        if sample:
                            buf = sample.get_buffer()
                            caps = sample.get_caps()
                            s = caps.get_structure(0)
                            w, h = s.get_value("width"), s.get_value("height")
                            ok, map_info = buf.map(Gst.MapFlags.READ)
                            if ok:
                                arr = np.frombuffer(map_info.data, dtype=np.uint8).reshape((h, w, 3)).copy()
                                buf.unmap(map_info)
                                with self.lock:
                                    self.latest_frame = Image.fromarray(arr)
                        return Gst.FlowReturn.OK

                    appsink.connect("new-sample", on_new_sample)
                    pipeline.set_state(Gst.State.PLAYING)
                    self.running = True

                    for _ in range(30):
                        if self.get_frame() is not None:
                            break
                        time.sleep(0.1)

                    on_ready_callback()

                def on_select_response(response, results):
                    if response != 0:
                        loop.quit()
                        on_error_callback("Auswahl abgebrochen.")
                        return
                    tok = f"start_{ts}"
                    call_portal_method(
                        "Start",
                        GLib.Variant("(osa{sv})", (session_path, "", {"handle_token": GLib.Variant("s", tok)})),
                        tok,
                        on_start_response,
                    )

                def on_create_response(response, results):
                    if response != 0:
                        loop.quit()
                        on_error_callback("Konnte Portal-Session nicht erstellen.")
                        return
                    tok = f"sel_{ts}"
                    call_portal_method(
                        "SelectSources",
                        GLib.Variant(
                            "(oa{sv})",
                            (
                                session_path,
                                {
                                    "handle_token": GLib.Variant("s", tok),
                                    "types": GLib.Variant("u", 1 | 2),
                                    "multiple": GLib.Variant("b", False),
                                },
                            ),
                        ),
                        tok,
                        on_select_response,
                    )

                create_tok = f"create_{ts}"
                call_portal_method(
                    "CreateSession",
                    GLib.Variant(
                        "(a{sv})",
                        (
                            {
                                "handle_token": GLib.Variant("s", create_tok),
                                "session_handle_token": GLib.Variant("s", session_token),
                            },
                        ),
                    ),
                    create_tok,
                    on_create_response,
                )
                loop.run()
            except Exception as e:
                on_error_callback(str(e))

        threading.Thread(target=_worker, daemon=True).start()

    def get_frame(self):
        if not self.is_wayland:
            import mss
            with mss.mss() as sct:
                monitor = sct.monitors[1]
                sct_img = sct.grab(monitor)
                return Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")

        with self.lock:
            return self.latest_frame.copy() if self.latest_frame is not None else None


class RegionSelector:
    def __init__(self, parent, screenshot, callback):
        self.top = tk.Toplevel(parent)
        self.top.title("Schachbrett exakt einrahmen (nur die 64 Felder)")
        self.callback = callback
        self.orig_img = screenshot

        max_w, max_h = 1200, 750
        self.scale = min(max_w / screenshot.width, max_h / screenshot.height, 1.0)
        disp_w = int(screenshot.width * self.scale)
        disp_h = int(screenshot.height * self.scale)

        disp_img = screenshot.resize((disp_w, disp_h), Image.Resampling.LANCZOS)
        self.tk_img = ImageTk.PhotoImage(disp_img)

        tk.Label(
            self.top,
            text="Ziehe ein Rechteck exakt von der oberen linken Ecke bis zur unteren rechten Ecke der 64 Felder:",
            font=("Segoe UI", 10, "bold"), bg="#111111", fg="#ffffff", pady=6
        ).pack(fill="x")

        self.canvas = tk.Canvas(self.top, width=disp_w, height=disp_h, cursor="cross", highlightthickness=0)
        self.canvas.pack()
        self.canvas.create_image(0, 0, anchor="nw", image=self.tk_img)

        self.start_x = None
        self.start_y = None
        self.rect = None

        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)

    def on_press(self, event):
        self.start_x = event.x
        self.start_y = event.y
        self.rect = self.canvas.create_rectangle(
            self.start_x, self.start_y, self.start_x, self.start_y,
            outline="#00ff66", width=3
        )

    def on_drag(self, event):
        self.canvas.coords(self.rect, self.start_x, self.start_y, event.x, event.y)

    def on_release(self, event):
        x1 = int(min(self.start_x, event.x) / self.scale)
        y1 = int(min(self.start_y, event.y) / self.scale)
        x2 = int(max(self.start_x, event.x) / self.scale)
        y2 = int(max(self.start_y, event.y) / self.scale)
        self.top.destroy()
        if (x2 - x1) > 60 and (y2 - y1) > 60:
            self.callback((x1, y1, x2, y2))


class ChessAnalyzerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Chess-Vision | Stockfish Analyzer")
        self.root.configure(bg="#ffffff")
        self.root.resizable(False, False)

        self.board = chess.Board()
        self.selected_square = None
        self.best_move = None
        self.board_bbox = None
        self.preview_tk_img = None
        self.stable_grid = None
        self.stable_count = 0

        self.analyze_for_white = tk.BooleanVar(value=True)
        self.auto_watch = tk.BooleanVar(value=False)

        self.sprite_templates = self.load_official_sprites()
        self.grabber = WaylandScreenGrabber()
        self.engine = self.init_engine()

        self.setup_ui()
        self.draw_board()
        self.analyze_position()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def load_official_sprites(self):
        """
        Lädt die 12 originalen Chess.com-Neo-Figuren-PNGs herunter (nur beim ersten Start)
        und erzeugt daraus Kanten- und Graustufen-Templates für 100% exaktes Matching.
        """
        os.makedirs(SPRITE_DIR, exist_ok=True)
        templates = {}

        for (color, pt), code in PIECE_CODES.items():
            png_path = os.path.join(SPRITE_DIR, f"{code}.png")
            if not os.path.exists(png_path):
                try:
                    req = urllib.request.Request(
                        f"{CHESSCOM_CDN}/{code}.png",
                        headers={"User-Agent": "Mozilla/5.0"}
                    )
                    with urllib.request.urlopen(req, timeout=10) as resp, open(png_path, "wb") as f:
                        f.write(resp.read())
                except Exception as e:
                    print(f"Warnung: Konnte {code}.png nicht laden: {e}")
                    continue

            rgba = cv2.imread(png_path, cv2.IMREAD_UNCHANGED)
            if rgba is None:
                continue

            # Auf 48x48 skalieren (identisch zur Zellgröße im Scanner)
            rgba_48 = cv2.resize(rgba, (48, 48), interpolation=cv2.INTER_AREA)
            alpha = rgba_48[:, :, 3].astype(np.float32) / 255.0
            bgr = rgba_48[:, :, :3]
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)

            # Auf neutralen Mittelgrau-Hintergrund (160) compositen, damit helle & dunkle Felder gleich gut matchen
            comp = (gray * alpha + 160.0 * (1.0 - alpha)).astype(np.uint8)
            edges = cv2.Canny(comp[4:44, 4:44], 60, 160)
            edges_blur = cv2.GaussianBlur(edges.astype(np.float32), (3, 3), 0)

            templates[(color, pt)] = {
                "gray": comp[6:42, 6:42],
                "edges": edges_blur,
                "alpha": (alpha[6:42, 6:42] > 0.3).astype(np.uint8),
            }

        return templates

    def init_engine(self):
        try:
            engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
            engine.configure({"Threads": 4, "Hash": 256})
            return engine
        except Exception as e:
            messagebox.showerror("Engine Fehler", f"Konnte Stockfish nicht starten:\n{e}")
            return None

    def restart_engine_if_needed(self):
        if self.engine is None:
            self.engine = self.init_engine()
            return
        try:
            self.engine.ping()
        except Exception:
            try:
                self.engine.quit()
            except Exception:
                pass
            self.engine = self.init_engine()

    def setup_ui(self):
        main_frame = tk.Frame(self.root, bg="#ffffff", padx=20, pady=20)
        main_frame.pack()

        self.square_size = 70
        board_px = self.square_size * 8
        self.canvas = tk.Canvas(
            main_frame, width=board_px, height=board_px,
            highlightthickness=1, highlightbackground="#cccccc"
        )
        self.canvas.grid(row=0, column=0, padx=(0, 20))
        self.canvas.bind("<Button-1>", self.on_square_click)

        control_frame = tk.Frame(main_frame, bg="#ffffff", width=290)
        control_frame.grid(row=0, column=1, sticky="ns")

        tk.Label(
            control_frame, text="Chess-Vision",
            font=("Segoe UI", 14, "bold"), bg="#ffffff", fg="#111111"
        ).pack(anchor="w", pady=(0, 10))

        vision_box = tk.Frame(control_frame, bg="#f4f6f8", padx=12, pady=10, highlightbackground="#e0e0e0", highlightthickness=1)
        vision_box.pack(fill="x", pady=(0, 12))

        tk.Label(
            vision_box, text="BILDSCHIRM-ERKENNUNG",
            font=("Segoe UI", 8, "bold"), bg="#f4f6f8", fg="#666666"
        ).pack(anchor="w", pady=(0, 6))

        tk.Button(
            vision_box, text="1. Bildschirm/Fenster verbinden",
            command=self.connect_and_select,
            font=("Segoe UI", 9, "bold"), bg="#0052cc", fg="#ffffff", relief="flat", pady=6, cursor="hand2"
        ).pack(fill="x", pady=(0, 5))

        tk.Button(
            vision_box, text="2. Brett-Ausschnitt neu wählen",
            command=self.open_region_selector,
            font=("Segoe UI", 9), bg="#ffffff", fg="#111111", relief="solid", bd=1, pady=4, cursor="hand2"
        ).pack(fill="x", pady=(0, 6))

        tk.Checkbutton(
            vision_box, text="Live-Überwachung (alle 0.4s)",
            variable=self.auto_watch, command=self.toggle_auto_watch,
            bg="#f4f6f8", fg="#111111", selectcolor="#f4f6f8", font=("Segoe UI", 9)
        ).pack(anchor="w")

        self.status_label = tk.Label(
            vision_box, text=f"Sprites: {len(self.sprite_templates)}/12 geladen",
            font=("Segoe UI", 8), bg="#f4f6f8", fg="#555555"
        )
        self.status_label.pack(anchor="w", pady=(4, 4))

        self.preview_canvas = tk.Canvas(vision_box, width=160, height=160, bg="#e0e0e0", highlightthickness=1, highlightbackground="#cccccc")
        self.preview_canvas.pack(pady=(2, 0))

        tk.Label(
            control_frame, text="Ich spiele als:",
            font=("Segoe UI", 10, "bold"), bg="#ffffff", fg="#444444"
        ).pack(anchor="w", pady=(6, 0))

        color_row = tk.Frame(control_frame, bg="#ffffff")
        color_row.pack(anchor="w", pady=(2, 10))

        tk.Radiobutton(
            color_row, text="Weiß", variable=self.analyze_for_white,
            value=True, command=self.on_color_change,
            bg="#ffffff", fg="#111111", selectcolor="#ffffff", font=("Segoe UI", 10)
        ).pack(side="left", padx=(0, 15))

        tk.Radiobutton(
            color_row, text="Schwarz", variable=self.analyze_for_white,
            value=False, command=self.on_color_change,
            bg="#ffffff", fg="#111111", selectcolor="#ffffff", font=("Segoe UI", 10)
        ).pack(side="left")

        result_box = tk.Frame(control_frame, bg="#f4f6f8", padx=12, pady=10, highlightbackground="#e0e0e0", highlightthickness=1)
        result_box.pack(fill="x", pady=(0, 12))

        tk.Label(
            result_box, text="BESTER ZUG (STOCKFISH)",
            font=("Segoe UI", 8, "bold"), bg="#f4f6f8", fg="#666666"
        ).pack(anchor="w")

        self.move_label = tk.Label(
            result_box, text="Berechne...",
            font=("Segoe UI", 18, "bold"), bg="#f4f6f8", fg="#0052cc"
        )
        self.move_label.pack(anchor="w", pady=(2, 2))

        self.eval_label = tk.Label(
            result_box, text="Bewertung: 0.00",
            font=("Segoe UI", 10), bg="#f4f6f8", fg="#333333"
        )
        self.eval_label.pack(anchor="w")

        btn_style = {"font": ("Segoe UI", 9), "bg": "#f0f0f0", "fg": "#111111", "relief": "flat", "pady": 5, "cursor": "hand2"}
        tk.Button(control_frame, text="Stellung jetzt übernehmen", command=self.force_full_sync, **btn_style).pack(fill="x", pady=(0, 5))
        tk.Button(control_frame, text="Zug zurücknehmen", command=self.undo_move, **btn_style).pack(fill="x", pady=(0, 5))
        tk.Button(control_frame, text="Brett zurücksetzen", command=self.reset_board, **btn_style).pack(fill="x")

    def connect_and_select(self):
        if self.grabber.running:
            self.open_region_selector()
            return

        self.status_label.config(text="Status: Warte auf Freigabe...", fg="#0052cc")
        self.grabber.start_portal_stream(
            on_ready_callback=lambda: self.root.after(0, self.open_region_selector),
            on_error_callback=lambda err: self.root.after(0, lambda: messagebox.showerror("Stream Fehler", err))
        )

    def open_region_selector(self):
        frame = self.grabber.get_frame()
        if frame is None:
            messagebox.showinfo("Hinweis", "Bitte zuerst auf '1. Bildschirm/Fenster verbinden' klicken.")
            return
        RegionSelector(self.root, frame, self.on_region_selected)

    def on_region_selected(self, bbox):
        self.board_bbox = bbox
        self.stable_grid = None
        self.stable_count = 0
        self.auto_watch.set(True)
        self.scan_screen_once(force_sync=True)
        self._auto_watch_loop()

    def force_full_sync(self):
        self.stable_grid = None
        self.scan_screen_once(force_sync=True)

    def toggle_auto_watch(self):
        if self.auto_watch.get():
            self._auto_watch_loop()

    def _auto_watch_loop(self):
        if self.auto_watch.get() and self.board_bbox:
            self.scan_screen_once(force_sync=False)
            self.root.after(400, self._auto_watch_loop)

    def update_preview(self, cropped_pil, detected_grid):
        thumb = cropped_pil.resize((160, 160), Image.Resampling.BILINEAR)
        self.preview_tk_img = ImageTk.PhotoImage(thumb)
        self.preview_canvas.delete("all")
        self.preview_canvas.create_image(0, 0, anchor="nw", image=self.preview_tk_img)

        for i in range(1, 8):
            pos = i * 20
            self.preview_canvas.create_line(pos, 0, pos, 160, fill="#00ff66", width=1)
            self.preview_canvas.create_line(0, pos, 160, pos, fill="#00ff66", width=1)

        for r in range(8):
            for c in range(8):
                val = detected_grid[r, c]
                if val != 0:
                    cx, cy = c * 20 + 10, r * 20 + 10
                    dot_color = "#0088ff" if val == 1 else "#ff2222"
                    self.preview_canvas.create_oval(cx - 4, cy - 4, cx + 4, cy + 4, fill=dot_color, outline="#ffffff")

    def _get_cell_bg_and_mask(self, cell_48x48):
        bg_samples = np.concatenate([
            cell_48x48[2:7, 2:8].flatten(),
            cell_48x48[2:7, 40:46].flatten(),
        ])
        bg_val = float(np.median(bg_samples))

        diff = np.abs(cell_48x48.astype(np.float32) - bg_val)
        mask = (diff > 28.0).astype(np.uint8)
        mask[:3, :] = 0
        mask[45:, :] = 0
        mask[:, :3] = 0
        mask[:, 45:] = 0

        return bg_val, mask

    def classify_board_image(self, pil_img):
        """
        Erkennt:
          0 = leer
          1 = weiße Figur
         -1 = schwarze Figur
        """
        resized = pil_img.resize((384, 384), Image.Resampling.BILINEAR)
        gray = cv2.cvtColor(np.array(resized), cv2.COLOR_RGB2GRAY)

        grid = np.zeros((8, 8), dtype=int)

        for r in range(8):
            for c in range(8):
                cell = gray[r * 48:(r + 1) * 48, c * 48:(c + 1) * 48]
                bg_val, mask = self._get_cell_bg_and_mask(cell)

                center_mask = mask[10:38, 10:38]
                fg_pixels = int(np.sum(center_mask))

                if fg_pixels >= 55:
                    piece_vals = cell[10:38, 10:38][center_mask > 0]
                    # Weiße Figuren haben einen hellen Kern (Median > 195), schwarze Figuren sind dunkel (< 120)
                    med_val = float(np.median(piece_vals))
                    if med_val >= 175.0:
                        grid[r, c] = 1   # Weiß
                    else:
                        grid[r, c] = -1  # Schwarz

        return grid, gray

    def _classify_piece_on_cell(self, cell_48x48, r_norm, c_norm, color):
        """
        Vergleicht die Zelle direkt mit den geladenen offiziellen Chess.com-Figuren-Sprites
        (kombiniert Kanten-Ähnlichkeit + Alpha-Masken-Überlappung).
        """
        cell_edges = cv2.Canny(cell_48x48[4:44, 4:44], 60, 160)
        cell_edges_blur = cv2.GaussianBlur(cell_edges.astype(np.float32), (3, 3), 0)
        _, cell_mask = self._get_cell_bg_and_mask(cell_48x48)
        cell_mask_36 = cell_mask[6:42, 6:42]

        best_pt = chess.PAWN if 1 <= r_norm <= 6 else chess.ROOK
        best_score = -999.0

        for (tpl_color, tpl_pt), tpl_data in self.sprite_templates.items():
            if tpl_color != color:
                continue
            if tpl_pt == chess.PAWN and r_norm in (0, 7):
                continue

            # 1. Kanten-Korrelation (sehr scharf für Krone, Springer-Kopf, Turm-Zinnen, Läufer-Kreuz)
            res_edge = cv2.matchTemplate(cell_edges_blur, tpl_data["edges"], cv2.TM_CCOEFF_NORMED)
            edge_score = float(np.max(res_edge))

            # 2. Silhouetten-IoU (unterscheidet perfekt kleine Bauern von hohen Figuren)
            tpl_alpha = tpl_data["alpha"]
            inter = float(np.sum((cell_mask_36 > 0) & (tpl_alpha > 0)))
            union = float(np.sum((cell_mask_36 > 0) | (tpl_alpha > 0)))
            iou_score = (inter / union) if union > 0 else 0.0

            total_score = 0.65 * edge_score + 0.35 * iou_score
            if total_score > best_score:
                best_score = total_score
                best_pt = tpl_pt

        return best_pt

    def _reconstruct_full_board_from_image(self, gray_384, raw_grid):
        is_white = self.analyze_for_white.get()
        if not is_white:
            img_norm = cv2.rotate(gray_384, cv2.ROTATE_180)
            norm_grid = np.rot90(raw_grid, 2)
        else:
            img_norm = gray_384
            norm_grid = raw_grid

        new_board = chess.Board(None)

        for r in range(8):
            for c in range(8):
                val = norm_grid[r, c]
                if val == 0:
                    continue
                color = chess.WHITE if val == 1 else chess.BLACK
                cell = img_norm[r * 48:(r + 1) * 48, c * 48:(c + 1) * 48]
                pt = self._classify_piece_on_cell(cell, r, c, color)
                sq = chess.square(c, 7 - r)
                new_board.set_piece_at(sq, chess.Piece(pt, color))

        for color in (chess.WHITE, chess.BLACK):
            kings = list(new_board.pieces(chess.KING, color))
            if len(kings) > 1:
                for extra_sq in kings[1:]:
                    new_board.set_piece_at(extra_sq, chess.Piece(chess.QUEEN, color))
            elif len(kings) == 0:
                preferred = [chess.E1, chess.G1, chess.C1, chess.D1] if color == chess.WHITE else [chess.E8, chess.G8, chess.C8, chess.D8]
                placed = False
                for psq in preferred:
                    p = new_board.piece_at(psq)
                    if p and p.color == color:
                        new_board.set_piece_at(psq, chess.Piece(chess.KING, color))
                        placed = True
                        break
                if not placed:
                    for sq in chess.SQUARES:
                        p = new_board.piece_at(sq)
                        if p and p.color == color:
                            new_board.set_piece_at(sq, chess.Piece(chess.KING, color))
                            break

        new_board.turn = chess.WHITE if is_white else chess.BLACK
        new_board.castling_rights = new_board.clean_castling_rights()
        new_board.ep_square = None

        if not new_board.is_valid():
            new_board.turn = not new_board.turn

        self.board = new_board
        self.status_label.config(text="Stellung übernommen!", fg="#007700")
        self.analyze_position()
        self.draw_board()

    def scan_screen_once(self, force_sync=False):
        if not self.board_bbox:
            return
        frame = self.grabber.get_frame()
        if frame is None:
            return

        cropped = frame.crop(self.board_bbox)
        raw_grid, gray_384 = self.classify_board_image(cropped)
        self.update_preview(cropped, raw_grid)

        if not force_sync:
            if self.stable_grid is not None and np.array_equal(raw_grid, self.stable_grid):
                self.stable_count += 1
            else:
                self.stable_grid = raw_grid.copy()
                self.stable_count = 1

            if self.stable_count < 2:
                return

        norm_grid = raw_grid if self.analyze_for_white.get() else np.rot90(raw_grid, 2)

        start_board = chess.Board()
        if self._grid_similarity(start_board, norm_grid) >= 62:
            if self.board.fen() != start_board.fen():
                self.board = start_board
                self.analyze_position()
                self.draw_board()
            self.status_label.config(text="Live: Startstellung erkannt", fg="#007700")
            return

        curr_sim = self._grid_similarity(self.board, norm_grid)
        best_seq = None
        best_sim = curr_sim

        if self.board.is_valid():
            for m1 in self.board.legal_moves:
                b1 = self.board.copy()
                b1.push(m1)
                sim1 = self._grid_similarity(b1, norm_grid)
                if sim1 > best_sim and sim1 >= 58:
                    best_sim = sim1
                    best_seq = [m1]

                for m2 in b1.legal_moves:
                    b2 = b1.copy()
                    b2.push(m2)
                    sim2 = self._grid_similarity(b2, norm_grid)
                    if sim2 > best_sim and sim2 >= 59:
                        best_sim = sim2
                        best_seq = [m1, m2]

        if best_seq:
            last_san = ""
            for mv in best_seq:
                last_san = self.board.san(mv)
                self.board.push(mv)
            self.status_label.config(text=f"Zug: {last_san} ({best_sim}/64)", fg="#007700")
            self.analyze_position()
            self.draw_board()
            return

        if curr_sim < 55 or force_sync:
            self._reconstruct_full_board_from_image(gray_384, raw_grid)

    def _grid_similarity(self, board_obj, norm_grid):
        matches = 0
        for r in range(8):
            for c in range(8):
                sq = chess.square(c, 7 - r)
                p = board_obj.piece_at(sq)
                expected = 0 if p is None else (1 if p.color == chess.WHITE else -1)
                if expected == norm_grid[r, c]:
                    matches += 1
        return matches

    def draw_board(self):
        self.canvas.delete("all")
        light_color = "#f0d9b5"
        dark_color = "#b58863"
        highlight_color = "#f6f669"

        for row in range(8):
            for col in range(8):
                if self.analyze_for_white.get():
                    square = chess.square(col, 7 - row)
                else:
                    square = chess.square(7 - col, row)

                x1 = col * self.square_size
                y1 = row * self.square_size
                x2 = x1 + self.square_size
                y2 = y1 + self.square_size

                color = light_color if (row + col) % 2 == 0 else dark_color
                if self.selected_square == square:
                    color = highlight_color

                self.canvas.create_rectangle(x1, y1, x2, y2, fill=color, outline="")

                piece = self.board.piece_at(square)
                if piece:
                    cx = x1 + self.square_size / 2
                    cy = y1 + self.square_size / 2
                    symbol = SOLID_SYMBOLS[piece.symbol().lower()]

                    if piece.color == chess.WHITE:
                        for dx, dy in [(-1, -1), (-1, 1), (1, -1), (1, 1), (0, -1), (0, 1), (-1, 0), (1, 0)]:
                            self.canvas.create_text(cx + dx, cy + dy, text=symbol, font=("DejaVu Sans", 38), fill="#111111")
                        self.canvas.create_text(cx, cy, text=symbol, font=("DejaVu Sans", 38), fill="#ffffff")
                    else:
                        self.canvas.create_text(cx, cy, text=symbol, font=("DejaVu Sans", 38), fill="#111111")

        if self.best_move:
            self.draw_arrow(self.best_move.from_square, self.best_move.to_square)

    def draw_arrow(self, from_sq, to_sq):
        if self.analyze_for_white.get():
            from_col = chess.square_file(from_sq)
            from_row = 7 - chess.square_rank(from_sq)
            to_col = chess.square_file(to_sq)
            to_row = 7 - chess.square_rank(to_sq)
        else:
            from_col = 7 - chess.square_file(from_sq)
            from_row = chess.square_rank(from_sq)
            to_col = 7 - chess.square_file(to_sq)
            to_row = chess.square_rank(to_sq)

        x1 = from_col * self.square_size + self.square_size / 2
        y1 = from_row * self.square_size + self.square_size / 2
        x2 = to_col * self.square_size + self.square_size / 2
        y2 = to_row * self.square_size + self.square_size / 2

        self.canvas.create_line(x1, y1, x2, y2, fill="#0066ff", width=6, arrow=tk.LAST, arrowshape=(16, 20, 6))

    def on_square_click(self, event):
        col = event.x // self.square_size
        row = event.y // self.square_size
        if not (0 <= col < 8 and 0 <= row < 8):
            return

        if self.analyze_for_white.get():
            clicked_square = chess.square(col, 7 - row)
        else:
            clicked_square = chess.square(7 - col, row)

        clicked_piece = self.board.piece_at(clicked_square)

        if self.selected_square is None:
            if clicked_piece and clicked_piece.color == self.board.turn:
                self.selected_square = clicked_square
        else:
            if self.selected_square == clicked_square:
                self.selected_square = None
            else:
                move = chess.Move(self.selected_square, clicked_square)
                moving_piece = self.board.piece_at(self.selected_square)
                if moving_piece and moving_piece.piece_type == chess.PAWN and chess.square_rank(clicked_square) in (0, 7):
                    move = chess.Move(self.selected_square, clicked_square, promotion=chess.QUEEN)

                if move in self.board.legal_moves:
                    self.board.push(move)
                    self.analyze_position()
                elif clicked_piece and clicked_piece.color == self.board.turn:
                    self.selected_square = clicked_square
                    self.draw_board()
                    return
                self.selected_square = None

        self.draw_board()

    def on_color_change(self):
        self.selected_square = None
        self.stable_grid = None
        self.board.turn = chess.WHITE if self.analyze_for_white.get() else chess.BLACK
        self.analyze_position()
        self.draw_board()

    def analyze_position(self):
        self.restart_engine_if_needed()
        if not self.engine:
            return

        my_color = chess.WHITE if self.analyze_for_white.get() else chess.BLACK

        if not self.board.is_valid():
            self.best_move = None
            self.move_label.config(text="Ausschnitt prüfen", fg="#cc6600")
            self.eval_label.config(text="Brett-Rahmen exakt auf 8x8 ziehen")
            return

        if self.board.is_game_over():
            self.best_move = None
            self.move_label.config(text="Spielende", fg="#111111")
            self.eval_label.config(text=self.board.result())
            return

        if self.board.turn != my_color:
            self.best_move = None
            self.move_label.config(text="Gegner zieht...", fg="#777777")
            self.eval_label.config(text="Warte auf Zug auf dem Brett")
            return

        try:
            info = self.engine.analyse(self.board, chess.engine.Limit(time=0.25))
            self.best_move = info["pv"][0]
            san_move = self.board.san(self.best_move)

            score = info["score"].white()
            if score.is_mate():
                eval_text = f"Matt in {abs(score.mate())} Zügen"
            else:
                cp = score.score() / 100.0
                eval_text = f"Bewertung: {cp:+.2f}"

            self.move_label.config(text=san_move, fg="#0052cc")
            self.eval_label.config(text=eval_text)
        except Exception as e:
            self.best_move = None
            self.restart_engine_if_needed()
            self.move_label.config(text="Neu berechnen...", fg="#cc0000")
            self.eval_label.config(text=str(e)[:30])

    def undo_move(self):
        if len(self.board.move_stack) > 0:
            self.board.pop()
            self.stable_grid = None
            self.analyze_position()
            self.draw_board()

    def reset_board(self):
        self.board.reset()
        self.selected_square = None
        self.stable_grid = None
        self.analyze_position()
        self.draw_board()

    def on_close(self):
        self.auto_watch.set(False)
        if self.engine:
            try:
                self.engine.quit()
            except Exception:
                pass
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = ChessAnalyzerApp(root)
    root.mainloop()