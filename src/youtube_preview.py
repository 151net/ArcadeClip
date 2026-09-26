"""Debounced range selection and a single low-resolution remote frame."""
from copy import deepcopy
from pathlib import Path
import math
import tempfile

from PIL import Image
from PIL.ImageQt import ImageQt
import av
from PySide6.QtCore import Qt, QTimer, QEvent
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton

from i18n import tr
from media import format_time, parse_time, oriented_image
from preview import VideoCanvas
from tasks import Task
from timeline import RangeTimeline


def fetch_preview(metadata, seconds, cancel, report):
    """Keep only the last fragment in memory; never fetch a separate audio track."""
    from live import _https, _segment, parse_window
    from sources import find_tool, run_command
    from fragments import selected_tracks
    from analysis_common import check_cancel
    check_cancel(cancel)
    duration = metadata.get('duration')
    if (type(seconds) not in (int, float) or not math.isfinite(seconds)
            or type(duration) not in (int, float) or not math.isfinite(duration)
            or not 0 <= seconds <= duration or duration <= 0):
        raise ValueError(tr('영상 길이 안에서 구간을 선택해 주세요.'))
    seconds = min(seconds, max(0, duration - .1))
    dvr = metadata.get('window', {}).get('dvr')
    fmt = dvr['preview'] if dvr else metadata.get('_preview_format')
    if not fmt:
        raise ValueError(tr('미리보기용 영상 형식을 찾을 수 없습니다. 정보를 다시 확인해 주세요.'))
    headers = fmt.get('http_headers') or {}
    segment = None
    if dvr:
        from live_dvr import locate
        item = locate(dvr, seconds, cancel)
        segment = dict(url=item['url'], init=None, duration=item['end'] - item['start'])
        offset = dvr['origin'] + seconds - item['start']
        metadata['_preview_chunk'] = ((None, item['url']), item['body'])
    elif fmt.get('protocol') in ('m3u8', 'm3u8_native'):
        if '_preview_window' not in metadata:
            url = _https('', fmt['url'])
            body = _segment(url, cancel, 8 * 1024 * 1024, headers)
            if not metadata.get('is_live') and b'#EXT-X-ENDLIST' not in body:
                raise ValueError(tr('아직 처리 중인 방송입니다. 처리가 끝난 뒤 정보를 다시 확인해 주세요.'))
            metadata['_preview_window'] = parse_window(body, url)
        window = metadata['_preview_window']
        if metadata.get('is_live'):
            origin = metadata['window'].get('start_utc')
            if origin is None or window['start_utc'] is None:
                raise ValueError(tr('라이브 시각의 시간대를 확인할 수 없습니다.'))
            target, key = origin + seconds, 'start_utc'
        else:
            target, key = seconds, 'start'
        segment = next((s for s in window['segments'] if s[key] <= target < s[key] + s['duration']), None)
        if segment is None:
            raise ValueError(tr('선택한 라이브 조각이 만료됐거나 연결이 끊겼습니다. 정보를 다시 조회해 주세요.'))
        offset = target - segment[key]
    elif fmt.get('protocol') == 'http_dash_segments':
        tracks = selected_tracks([fmt], seconds, min(duration, seconds + .001), cancel)
        if tracks:
            segment = tracks[0]['segments'][0]
            offset = seconds - segment['start']
    with tempfile.TemporaryDirectory(prefix='arcade-preview-') as directory:
        path = Path(directory) / 'frame.png'
        if segment:
            cached = metadata.get('_preview_chunk')
            if cached is None or cached[0] != (segment['init'], segment['url']):
                init = _segment(segment['init'], cancel, 16 * 1024 * 1024, headers) if segment['init'] else b''
                body = init + _segment(segment['url'], cancel, 128 * 1024 * 1024, headers)
                check_cancel(cancel)
                metadata['_preview_chunk'] = ((segment['init'], segment['url']), body)
            else:
                body = cached[1]
            media = Path(directory) / ('segment.mp4' if segment['init'] else 'segment.ts')
            media.write_bytes(body)
            # Isolated TS fragments often cannot seek, even to their first frame.
            # Decode this one small fragment from its first PTS; clamp at its last frame.
            with av.open(str(media)) as container:
                if not container.streams.video:
                    raise ValueError(tr('영상 스트림을 찾을 수 없습니다.'))
                first_time, selected = None, None
                for frame in container.decode(video=0):
                    check_cancel(cancel)
                    if frame.pts is None:
                        continue
                    timestamp = float(frame.pts * frame.time_base)
                    if first_time is None:
                        first_time = timestamp
                    selected = frame
                    if timestamp - first_time + 1e-7 >= offset:
                        break
                if selected is None:
                    raise ValueError(tr('해당 시각의 프레임을 찾을 수 없습니다.'))
                image = oriented_image(selected)
        else:
            # Progressive files have no fragment index; FFmpeg seeks using HTTP range requests.
            url = _https('', fmt['url'])
            if any('\r' in str(value) or '\n' in str(value) for pair in headers.items() for value in pair):
                raise ValueError(tr('영상 정보를 다시 확인해 주세요.'))
            args = [find_tool('ffmpeg'), '-v', 'error', '-nostdin', '-n', '-rw_timeout', '10000000']
            if headers:
                args += ['-headers', ''.join(f'{key}: {value}\r\n' for key, value in headers.items())]
            run_command(args + ['-ss', str(seconds), '-i', url, '-map', '0:v:0', '-frames:v', '1',
                                '-an', '-vf', 'scale=-2:180', str(path)], cancel, report)
            with Image.open(path) as frame:
                image = frame.copy()
        check_cancel(cancel)
        image.thumbnail((640, 360))
        return image


class YouTubePreview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.metadata = None
        self.task = None
        self.revision = 0
        self.position = 0.
        self.ready = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.canvas = VideoCanvas()
        self.canvas.show_regions = False
        layout.addWidget(self.canvas, 1)
        self.status = QLabel(tr('위치를 옮긴 뒤 1초 기다리면 미리보기를 가져옵니다.'))
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.timeline = RangeTimeline()
        self.timeline.time_label = self.label
        self.timeline.setToolTip(tr('양끝 핸들: 시작·끝 조절 | 클릭: 미리보기 위치 | Ctrl+휠: 확대'))
        self.timeline.changed.connect(self.range_changed)
        self.timeline.seek.connect(self.seek)
        layout.addWidget(self.timeline)
        fields = QHBoxLayout()
        self.start, self.end = QLineEdit(), QLineEdit()
        for title, field in ((tr('시작'), self.start), (tr('끝'), self.end)):
            fields.addWidget(QLabel(title))
            field.setAccessibleName(title)
            fields.addWidget(field)
            field.editingFinished.connect(lambda field=field: self.edit_range(field))
        layout.addLayout(fields)
        buttons = QHBoxLayout()
        for title, action in ((tr('현재 위치를 시작으로'), lambda: self.mark(False)),
                              (tr('현재 위치를 끝으로'), lambda: self.mark(True)),
                              (tr('전체 구간'), self.select_whole),
                              (tr('전체 시간 보기'), self.timeline.fit)):
            button = QPushButton(title)
            button.clicked.connect(action)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.request_preview)

    def label(self, seconds):
        if (self.metadata or {}).get('is_live'):
            value = format_time(max(0, self.metadata['duration'] - seconds))
            return '-' + (value[3:] if value.startswith('00:') else value)
        return format_time(seconds)

    def read_time(self, text):
        if (self.metadata or {}).get('is_live'):
            text = text.strip().replace('−', '-')
            if not text.startswith('-'):
                raise ValueError(tr('라이브 시각은 -MM:SS 또는 -HH:MM:SS 형식으로 입력해 주세요.'))
            value = text[1:]
            return self.metadata['duration'] - parse_time('00:' + value if value.count(':') == 1 else value)
        return parse_time(text)

    def selected_times(self):
        start, end = self.read_time(self.start.text()), self.read_time(self.end.text())
        if not 0 <= start < end <= (self.metadata or {}).get('duration', 0):
            raise ValueError(tr('다운로드할 시작·끝을 영상 길이 안에서 지정해 주세요.'))
        return start, end

    def load(self, metadata):
        self.stop()
        self.metadata = deepcopy(metadata)
        self.canvas.set_image(QImage())
        self.timeline.manual_view = False
        self.timeline.extent = metadata.get('duration') or 1
        self.position = 0.
        self.select_whole()
        self.seek(max(0, self.timeline.extent - 1) if metadata.get('is_live') else 0)

    def update_range(self, start, end):
        self.start.setText(self.label(start))
        self.end.setText(self.label(end))
        self.timeline.set_values(0, self.metadata.get('duration') or 1, start, end, self.position)
        self.summary.setText(tr('선택 길이: {time}', time=format_time(end - start)))
        if self.metadata.get('is_live'):
            self.summary.setText(self.summary.text() + '\n' + tr('라이브 음수 시각은 조회한 최신 지점 기준입니다. 최신 구간은 정보 새로고침을 누르세요.'))

    def select_whole(self):
        if self.metadata:
            self.update_range(0, self.metadata.get('duration') or 0)

    def range_changed(self, start, end):
        old_start, _ = self.timeline.selection
        try:
            old_start = self.read_time(self.start.text())
        except ValueError:
            pass
        self.update_range(start, end)
        self.seek(start if abs(start - old_start) > .0001 else end)

    def edit_range(self, field):
        if not self.metadata:
            return
        try:
            start, end = self.selected_times()
            self.update_range(start, end)
            self.seek(start if field is self.start else end)
        except ValueError as error:
            self.status.setText(str(error))

    def mark(self, end):
        if not self.metadata:
            return
        field = self.end if end else self.start
        field.setText(self.label(self.position))
        self.edit_range(field)

    def seek(self, seconds):
        if not self.metadata:
            return
        self.position = max(0, min(self.metadata.get('duration') or 0, seconds))
        self.timeline.position = self.position
        self.timeline.update()
        self.stop()
        self.status.setText(tr('{time} · 1초 후 미리보기', time=self.label(self.position)))
        if self.isVisible() and self.isEnabled():
            self.timer.start()

    def stop(self):
        self.timer.stop()
        self.ready = False
        self.revision += 1
        if self.task:
            self.task.cancel.set()

    def request_preview(self):
        if not self.isVisible() or not self.isEnabled() or not self.metadata:
            return
        self.ready = True
        if self.task:
            return
        self.ready = False
        revision, metadata, seconds = self.revision, self.metadata, self.position
        self.status.setText(tr('{time} · 미리보기 가져오는 중…', time=self.label(seconds)))
        self.task = Task(lambda cancel, report: fetch_preview(metadata, seconds, cancel, report), self)
        self.task.finished.connect(lambda: self.finished(revision, seconds))
        self.task.start()

    def finished(self, revision, seconds):
        task, self.task = self.task, None
        task.wait()
        if revision == self.revision and not task.cancel.is_set():
            if task.error:
                self.status.setText(tr('미리보기 실패: {error}', error=str(task.error)))
            else:
                self.canvas.set_image(ImageQt(task.result).copy())
                self.status.setText(tr('{time} · 저해상도 미리보기', time=self.label(seconds)))
        task.deleteLater()
        if self.ready:
            self.request_preview()

    def showEvent(self, event):
        super().showEvent(event)
        if self.metadata:
            self.seek(self.position)

    def changeEvent(self, event):
        if event.type() == QEvent.Type.EnabledChange:
            if self.isEnabled() and self.metadata:
                self.seek(self.position)
            else:
                self.stop()
        super().changeEvent(event)

    def handle_key(self, key, modifiers):
        if not self.isEnabled() or not self.metadata:
            return False
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            step = 5 if modifiers & Qt.KeyboardModifier.ControlModifier else 1
            self.seek(self.position + (-step if key == Qt.Key.Key_Left else step))
        elif key in (Qt.Key.Key_Home, Qt.Key.Key_End):
            self.seek(0 if key == Qt.Key.Key_Home else self.metadata.get('duration') or 0)
        elif key in (Qt.Key.Key_I, Qt.Key.Key_O):
            self.mark(key == Qt.Key.Key_O)
        else:
            return False
        return True

    def hideEvent(self, event):
        self.stop()
        super().hideEvent(event)

    def closeEvent(self, event):
        self.release()
        super().closeEvent(event)

    def release(self):
        self.stop()
        if self.task:
            self.task.wait()
        self.canvas.release()
