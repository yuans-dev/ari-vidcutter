#!/usr/bin/python
import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QProcess, QRect, QSize, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QImage, QKeySequence, QPainter, QPalette, QPen, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaMetaData, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStyle,
    QVBoxLayout,
    QWidget,
)

VIDEO_FILTER = "Video Files (*.mp4 *.mkv *.avi *.mov *.webm *.flv *.wmv *.m4v *.mpeg *.mpg *.ts)"
THUMB_COUNT = 24
THUMB_HEIGHT = 64


def fmt_time(ms: int) -> str:
    ms = max(0, int(ms))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, milli = divmod(rem, 1000)
    return f"{h}:{m:02d}:{s:02d}.{milli:03d}"


class ThumbnailWorker(QThread):
    """Extracts evenly spaced preview frames with ffmpeg, one at a time."""

    thumbnail = Signal(int, QImage)

    def __init__(self, path: str, duration_ms: int, count: int, height: int, parent=None):
        super().__init__(parent)
        self._path = path
        self._duration = duration_ms
        self._count = count
        self._height = height

    def run(self):
        for i in range(self._count):
            if self.isInterruptionRequested():
                return
            t = (i + 0.5) / self._count * self._duration / 1000.0
            cmd = [
                "ffmpeg", "-v", "error",
                "-ss", f"{t:.3f}", "-i", self._path,
                "-frames:v", "1",
                "-vf", f"scale=-2:{self._height}",
                "-f", "image2pipe", "-vcodec", "png", "-",
            ]
            try:
                out = subprocess.run(cmd, capture_output=True, timeout=30).stdout
            except (OSError, subprocess.SubprocessError):
                continue
            img = QImage.fromData(out, "PNG")
            if not img.isNull():
                self.thumbnail.emit(i, img)


class TrimSlider(QWidget):
    """Filmstrip timeline with a playhead and two trim handles.

    Painted entirely from the widget palette so Kvantum controls the colours.
    """

    positionChanged = Signal(int)  # user seeked (ms)
    rangeChanged = Signal(int, int)  # start, end (ms)

    HANDLE_W = 10
    MIN_GAP_MS = 100

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duration = 0
        self._start = 0
        self._end = 0
        self._position = 0
        self._thumbs: dict[int, QPixmap] = {}
        self._drag = None  # "start" | "end" | "seek"
        self.setMinimumHeight(THUMB_HEIGHT + 16)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(THUMB_HEIGHT + 16)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    # -- public API ---------------------------------------------------
    def set_duration(self, ms: int):
        self._duration = ms
        self._start = 0
        self._end = ms
        self._position = 0
        self._thumbs.clear()
        self.update()
        self.rangeChanged.emit(self._start, self._end)

    def set_position(self, ms: int):
        if self._drag == "seek":
            return
        self._position = ms
        self.update()

    def set_thumbnail(self, index: int, image: QImage):
        self._thumbs[index] = QPixmap.fromImage(image)
        self.update()

    def set_start(self, ms: int):
        self._start = max(0, min(ms, self._end - self.MIN_GAP_MS))
        self.update()
        self.rangeChanged.emit(self._start, self._end)

    def set_end(self, ms: int):
        self._end = min(self._duration, max(ms, self._start + self.MIN_GAP_MS))
        self.update()
        self.rangeChanged.emit(self._start, self._end)

    def start(self) -> int:
        return self._start

    def end(self) -> int:
        return self._end

    # -- geometry helpers ---------------------------------------------
    def _track(self) -> QRect:
        return QRect(self.HANDLE_W, 8, max(1, self.width() - 2 * self.HANDLE_W), THUMB_HEIGHT)

    def _ms_to_x(self, ms: int) -> int:
        t = self._track()
        if self._duration <= 0:
            return t.left()
        return t.left() + round(ms / self._duration * t.width())

    def _x_to_ms(self, x: int) -> int:
        t = self._track()
        if self._duration <= 0:
            return 0
        frac = (x - t.left()) / t.width()
        return int(max(0.0, min(1.0, frac)) * self._duration)

    # -- painting -----------------------------------------------------
    def paintEvent(self, event):
        p = QPainter(self)
        pal = self.palette()
        track = self._track()

        p.fillRect(track, pal.base())

        # Filmstrip
        if self._thumbs:
            tile_w = track.width() / THUMB_COUNT
            for i, pix in self._thumbs.items():
                tile = QRect(track.left() + round(i * tile_w), track.top(),
                             round(tile_w) + 1, track.height())
                scaled = pix.scaled(tile.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                    Qt.TransformationMode.SmoothTransformation)
                p.save()
                p.setClipRect(tile)
                p.drawPixmap(tile.left() + (tile.width() - scaled.width()) // 2,
                             tile.top() + (tile.height() - scaled.height()) // 2, scaled)
                p.restore()

        if self._duration <= 0:
            p.setPen(pal.color(pal.ColorRole.PlaceholderText))
            p.drawText(track, Qt.AlignmentFlag.AlignCenter, "Open a video to begin")
            return

        xs = self._ms_to_x(self._start)
        xe = self._ms_to_x(self._end)

        # Dim everything outside the selection
        dim = QColor(pal.color(pal.ColorRole.Window))
        dim.setAlpha(190)
        p.fillRect(QRect(track.left(), track.top(), xs - track.left(), track.height()), dim)
        p.fillRect(QRect(xe, track.top(), track.right() + 1 - xe, track.height()), dim)

        # Selection border (top/bottom)
        hl = pal.color(pal.ColorRole.Highlight)
        p.fillRect(QRect(xs, track.top() - 3, xe - xs, 3), hl)
        p.fillRect(QRect(xs, track.bottom() + 1, xe - xs, 3), hl)

        # Handles
        grip = pal.color(pal.ColorRole.HighlightedText)
        for rect in (
            QRect(xs - self.HANDLE_W, track.top() - 3, self.HANDLE_W, track.height() + 6),
            QRect(xe, track.top() - 3, self.HANDLE_W, track.height() + 6),
        ):
            p.fillRect(rect, hl)
            p.setPen(QPen(grip, 2))
            cx = rect.center().x()
            p.drawLine(cx, rect.top() + 12, cx, rect.bottom() - 12)

        # Playhead
        px = self._ms_to_x(self._position)
        p.setPen(QPen(pal.color(pal.ColorRole.Text), 2))
        p.drawLine(px, 0, px, self.height())

    # -- interaction --------------------------------------------------
    def _hit(self, pos: QPoint):
        xs = self._ms_to_x(self._start)
        xe = self._ms_to_x(self._end)
        slop = 4
        if xs - self.HANDLE_W - slop <= pos.x() <= xs + slop:
            return "start"
        if xe - slop <= pos.x() <= xe + self.HANDLE_W + slop:
            return "end"
        return "seek"

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or self._duration <= 0:
            return
        self._drag = self._hit(e.position().toPoint())
        self._apply_drag(e.position().toPoint())

    def mouseMoveEvent(self, e):
        if self._duration <= 0:
            return
        pos = e.position().toPoint()
        if self._drag:
            self._apply_drag(pos)
        else:
            self.setCursor(Qt.CursorShape.SizeHorCursor if self._hit(pos) != "seek"
                           else Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, e):
        self._drag = None

    def _apply_drag(self, pos: QPoint):
        ms = self._x_to_ms(pos.x())
        if self._drag == "start":
            self.set_start(ms)
            self._position = self._start
            self.positionChanged.emit(self._start)
        elif self._drag == "end":
            self.set_end(ms)
            self._position = self._end
            self.positionChanged.emit(self._end)
        elif self._drag == "seek":
            self._position = ms
            self.update()
            self.positionChanged.emit(ms)


class PanelFrame(QFrame):
    """Frame that paints a 1px outline on all four sides using the palette."""

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setPen(self.palette().color(QPalette.ColorRole.Mid))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Ari")
        self.resize(900, 600)

        self._path = None
        self._duration = 0
        self._worker = None
        self._proc = None

        # --- Media ---
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)

        # --- Menu ---
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("File")

        open_action = QAction("Open", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self.open_file)

        self.save_action = QAction("Save Trimmed As...", self)
        self.save_action.setShortcut(QKeySequence.StandardKey.Save)
        self.save_action.setEnabled(False)
        self.save_action.triggered.connect(self.export)

        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)

        file_menu.addAction(open_action)
        file_menu.addAction(self.save_action)
        file_menu.addSeparator()
        file_menu.addAction(exit_action)

        # --- Layout ---
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # Top panel: video preview
        video_container = QFrame()
        video_layout = QVBoxLayout(video_container)
        video_layout.setContentsMargins(2, 2, 2, 2)
        self.video = QVideoWidget()
        self.video.setMinimumSize(320, 180)
        self.player.setVideoOutput(self.video)
        video_layout.addWidget(self.video)

        # Bottom panel: trimming controls
        trimming_controls_container = PanelFrame()
        trimming_controls_container.setFixedHeight(200)
        trimming_controls_container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        controls = QVBoxLayout(trimming_controls_container)
        controls.setContentsMargins(12, 12, 12, 12)
        controls.setSpacing(8)

        self.slider = TrimSlider()
        controls.addWidget(self.slider)

        style = self.style()

        row = QHBoxLayout()
        row.setSpacing(6)

        self.btn_prev = QPushButton()
        self.btn_prev.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_MediaSeekBackward))
        self.btn_prev.setToolTip("Previous frame  (,)")
        self.btn_prev.setShortcut(",")

        self.btn_play = QPushButton()
        self.btn_play.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.btn_play.setToolTip("Play / Pause  (Space)")
        self.btn_play.setShortcut(Qt.Key.Key_Space)

        self.btn_next = QPushButton()
        self.btn_next.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_MediaSeekForward))
        self.btn_next.setToolTip("Next frame  (.)")
        self.btn_next.setShortcut(".")

        self.btn_set_start = QPushButton("Set Start")
        self.btn_set_start.setToolTip("Trim start at playhead  (I)")
        self.btn_set_start.setShortcut("I")

        self.btn_set_end = QPushButton("Set End")
        self.btn_set_end.setToolTip("Trim end at playhead  (O)")
        self.btn_set_end.setShortcut("O")

        self.chk_reencode = QCheckBox("Frame-accurate (re-encode)")
        self.chk_reencode.setToolTip(
            "Off: fast stream copy, cuts snap to the nearest keyframe.\n"
            "On: re-encodes, exact cuts but slower."
        )

        self.btn_export = QPushButton("Export")

        for w in (self.btn_prev, self.btn_play, self.btn_next):
            row.addWidget(w)
        row.addSpacing(12)
        row.addWidget(self.btn_set_start)
        row.addWidget(self.btn_set_end)
        row.addStretch(1)
        row.addWidget(self.chk_reencode)
        row.addWidget(self.btn_export)
        controls.addLayout(row)

        self.time_label = QLabel()
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        controls.addWidget(self.time_label)

        layout.addWidget(video_container, 1)
        layout.addWidget(trimming_controls_container, 0)
        self.setCentralWidget(container)
        self.statusBar()

        # --- Signals ---
        self.btn_play.clicked.connect(self.toggle_play)
        self.btn_prev.clicked.connect(lambda: self.step_frame(-1))
        self.btn_next.clicked.connect(lambda: self.step_frame(+1))
        self.btn_set_start.clicked.connect(
            lambda: self.slider.set_start(self.player.position()))
        self.btn_set_end.clicked.connect(
            lambda: self.slider.set_end(self.player.position()))
        self.btn_export.clicked.connect(self.export)

        self.slider.positionChanged.connect(self.on_user_seek)
        self.slider.rangeChanged.connect(lambda *_: self.update_time_label())

        self.player.positionChanged.connect(self.on_position)
        self.player.durationChanged.connect(self.on_duration)
        self.player.playbackStateChanged.connect(self.on_state)
        self.player.errorOccurred.connect(
            lambda _err, msg: self.statusBar().showMessage(f"Playback error: {msg}"))

        self.set_controls_enabled(False)
        self.update_time_label()

    # ------------------------------------------------------------------
    def set_controls_enabled(self, on: bool):
        for w in (self.btn_prev, self.btn_play, self.btn_next, self.btn_set_start,
                  self.btn_set_end, self.btn_export, self.chk_reencode):
            w.setEnabled(on)
        self.save_action.setEnabled(on)

    def update_time_label(self):
        self.time_label.setText(
            f"Position {fmt_time(self.player.position())}   |   "
            f"Start {fmt_time(self.slider.start())}   |   "
            f"End {fmt_time(self.slider.end())}   |   "
            f"Length {fmt_time(self.slider.end() - self.slider.start())}   |   "
            f"Total {fmt_time(self._duration)}"
        )

    # --- File / media -------------------------------------------------
    def open_file(self, checked=False):
        file_path, _ = QFileDialog.getOpenFileName(self, "Open Video", "", VIDEO_FILTER)
        if file_path:
            self.load_path(file_path)

    def load_path(self, file_path: str):
        """Load a video by path (used by File > Open and by 'Open with...')."""
        if not Path(file_path).is_file():
            QMessageBox.warning(self, "Cannot open file", f"File not found:\n{file_path}")
            return

        if not shutil.which("ffmpeg"):
            QMessageBox.warning(self, "ffmpeg not found",
                                "ffmpeg must be installed and on your PATH for "
                                "frame previews and export.")

        self._stop_worker()
        self._path = file_path
        self._duration = 0
        self.setWindowTitle(f"Ari - {file_path.rsplit('/', 1)[-1]}")
        self.player.setSource(QUrl.fromLocalFile(file_path))
        self.player.pause()  # shows the first frame
        self.statusBar().showMessage(f"Loaded {file_path}")

    def on_duration(self, ms: int):
        if ms <= 0 or ms == self._duration:
            return
        self._duration = ms
        self.slider.set_duration(ms)
        self.set_controls_enabled(True)
        self.update_time_label()
        self._start_thumbnails()

    def _start_thumbnails(self):
        if not self._path or not shutil.which("ffmpeg"):
            return
        self._worker = ThumbnailWorker(self._path, self._duration, THUMB_COUNT, THUMB_HEIGHT, self)
        self._worker.thumbnail.connect(self.slider.set_thumbnail)
        self._worker.start()

    def _stop_worker(self):
        if self._worker and self._worker.isRunning():
            self._worker.requestInterruption()
            self._worker.wait(3000)
        self._worker = None

    # --- Playback -----------------------------------------------------
    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            return
        pos = self.player.position()
        if pos < self.slider.start() or pos >= self.slider.end() - 20:
            self.player.setPosition(self.slider.start())
        self.player.play()

    def on_state(self, state):
        icon = (QStyle.StandardPixmap.SP_MediaPause
                if state == QMediaPlayer.PlaybackState.PlayingState
                else QStyle.StandardPixmap.SP_MediaPlay)
        self.btn_play.setIcon(self.style().standardIcon(icon))

    def on_position(self, ms: int):
        # Stop at the end handle so the selection can be previewed
        if (self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
                and ms >= self.slider.end()):
            self.player.pause()
            self.player.setPosition(self.slider.end())
            ms = self.slider.end()
        self.slider.set_position(ms)
        self.update_time_label()

    def on_user_seek(self, ms: int):
        self.player.setPosition(ms)
        self.update_time_label()

    def frame_ms(self) -> float:
        fps = self.player.metaData().value(QMediaMetaData.Key.VideoFrameRate)
        try:
            fps = float(fps)
        except (TypeError, ValueError):
            fps = 0.0
        return 1000.0 / fps if fps > 0 else 1000.0 / 30.0

    def step_frame(self, direction: int):
        self.player.pause()
        target = self.player.position() + direction * self.frame_ms()
        self.player.setPosition(int(max(0, min(self._duration, round(target)))))

    # --- Export -------------------------------------------------------
    def export(self):
        if not self._path or self._proc is not None:
            return
        src = Path(self._path)
        default_path = str(src.with_name(f"{src.stem}_trimmed{src.suffix}"))
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Save Trimmed Video", default_path,
            "Video Files (*.mp4 *.mkv *.mov *.webm *.avi)")
        if not out_path:
            return

        start = self.slider.start() / 1000.0
        length = (self.slider.end() - self.slider.start()) / 1000.0

        args = ["-y", "-ss", f"{start:.3f}", "-i", self._path, "-t", f"{length:.3f}"]
        if self.chk_reencode.isChecked():
            pass  # ffmpeg's default encoders for the container
        else:
            args += ["-c", "copy", "-avoid_negative_ts", "make_zero"]
        args.append(out_path)

        self._proc = QProcess(self)
        self._proc.finished.connect(self._export_finished)
        self._proc.errorOccurred.connect(self._export_failed)
        self.btn_export.setEnabled(False)
        self.save_action.setEnabled(False)
        self.statusBar().showMessage("Exporting...")
        self._proc.start("ffmpeg", args)

    def _export_finished(self, code, _status):
        err = bytes(self._proc.readAllStandardError()).decode(errors="replace")
        self._proc.deleteLater()
        self._proc = None
        self.btn_export.setEnabled(True)
        self.save_action.setEnabled(True)
        if code == 0:
            self.statusBar().showMessage("Export complete")
        else:
            self.statusBar().showMessage("Export failed")
            QMessageBox.critical(self, "Export failed", err[-1500:] or f"ffmpeg exited with {code}")

    def _export_failed(self, _error):
        if self._proc is None or self._proc.state() != QProcess.ProcessState.NotRunning:
            return
        self._proc.deleteLater()
        self._proc = None
        self.btn_export.setEnabled(True)
        self.save_action.setEnabled(True)
        self.statusBar().showMessage("Could not run ffmpeg")

    def closeEvent(self, event):
        self._stop_worker()
        self.player.stop()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Ari")
    app.setDesktopFileName("ari")  # matches ari.desktop so the shell links window and launcher

    window = MainWindow()
    window.show()

    # "Open with..." passes the file as a command-line argument
    args = app.arguments()[1:]
    if args:
        window.load_path(args[0])

    sys.exit(app.exec())


if __name__ == "__main__":
    main()