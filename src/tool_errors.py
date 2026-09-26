"""Actionable errors from FFmpeg, ffprobe, and yt-dlp; raw details stay in diagnostics."""
import re
from i18n import tr


def error_kind(detail):
    text = detail.lower()
    # Match explicit diagnostics, never a generic exit code such as -5 / 4294967291.
    patterns = (
        ('certificate', r'certificate_verify_failed|certificate verify failed|certificate failed verification|certificate verification failed|unable to get local issuer certificate|certificate has expired|certificate is not yet valid|certificate is not trusted|self.signed certificate|hostname mismatch'),
        ('rate_limit', r'http(?: error| error code)?[ :]+429\b|429 too many requests'),
        ('forbidden', r'http(?: error| error code)?[ :]+403\b|403 forbidden'),
        ('remote_missing', r'http(?: error| error code)?[ :]+(?:404|410)\b|404 not found|410 gone'),
        ('login', r'sign in to confirm|login required|private video|members.only|age.restricted|authentication required'),
        ('unavailable', r'video unavailable|video is not available|not available in your country|has been removed'),
        ('network', r'timed out|connection (?:reset|refused)|network is unreachable|failed to resolve|name or service not known|getaddrinfo failed|temporary failure in name resolution'),
        ('disk_full', r'no space left on device|not enough space on the disk|disk full'),
        ('memory', r'cannot allocate memory|out of memory|not enough memory'),
        ('permission', r'permission denied|access is denied|access denied|being used by another process'),
        ('gpu', r'cannot load (?:nvcuda|nvencodeapi)|no capable devices found|driver does not support the required nvenc|failed to (?:initialise|initialize) (?:nvenc|cuda|mfx)|error initializing an internal mfx session|amf.*(?:failed|not supported)'),
        ('format', r'requested format is not available|signature extraction failed|nsig extraction failed|no supported javascript runtime|challenge solving failed'),
        ('media', r'invalid data found when processing input|moov atom not found|could not find codec parameters|decoder .* not found|unknown encoder'),
        ('file_missing', r'no such file or directory|cannot find the file specified'),
        ('io', r'input/output error|i/o error'),
    )
    return next((kind for kind, pattern in patterns if re.search(pattern, text)), 'unknown')


def error_message(kind):
    if kind == 'certificate':
        return tr('서버 인증서를 확인하지 못했습니다. Windows 날짜와 시간을 확인하고 Windows Update를 실행하세요. 계속 실패하면 최신 앱을 받아 다시 시도하세요.')
    if kind == 'rate_limit':
        return tr('서버가 요청을 제한했습니다. 동시 다운로드 수를 줄이고 잠시 후 다시 시도하세요.')
    if kind == 'forbidden':
        return tr('서버가 영상 접근을 거부했습니다. 정보 새로고침 후 다시 시도하세요. 계속 실패하면 브라우저에서 재생 가능한지 확인하세요.')
    if kind == 'remote_missing':
        return tr('서버에서 영상 또는 선택한 구간을 찾지 못했습니다. 정보를 새로고침하고 구간을 다시 선택하세요.')
    if kind == 'login':
        return tr('로그인이나 시청 권한이 필요한 영상입니다. 브라우저에서 시청 가능 여부를 확인하고, 내려받을 수 있는 다른 영상을 선택하세요.')
    if kind == 'unavailable':
        return tr('현재 사용할 수 없는 영상입니다. 브라우저에서 영상 공개 상태와 재생 가능 여부를 확인하세요.')
    if kind == 'network':
        return tr('서버에 연결하지 못했거나 응답이 늦어졌습니다. 인터넷 연결과 VPN·프록시 설정을 확인하고 다시 시도하세요.')
    if kind == 'disk_full':
        return tr('저장 공간이 부족합니다. 저장 폴더와 임시 폴더가 있는 드라이브의 공간을 확보한 뒤 다시 시도하세요.')
    if kind == 'memory':
        return tr('작업에 필요한 메모리가 부족합니다. 다른 프로그램을 닫고 동시 작업 수나 영상 해상도를 낮춰 다시 시도하세요.')
    if kind == 'permission':
        return tr('파일에 접근하지 못했습니다. 파일을 사용 중인 프로그램을 닫고 읽기·쓰기 권한이 있는 폴더를 선택하세요.')
    if kind == 'gpu':
        return tr('GPU 인코더를 시작하지 못했습니다. 저장 설정에서 GPU 인코딩을 끄고 다시 시도하거나 그래픽 드라이버를 업데이트하세요.')
    if kind == 'format':
        return tr('영상의 다운로드 형식을 처리하지 못했습니다. 정보를 새로고침하세요. 계속 실패하면 최신 앱을 받아 다시 시도하세요.')
    if kind == 'media':
        return tr('영상 형식을 읽거나 변환하지 못했습니다. 원본이 정상 재생되는지 확인하고, 다시 내려받거나 다른 형식의 영상을 선택하세요.')
    if kind == 'file_missing':
        return tr('필요한 파일을 찾지 못했습니다. 원본 영상과 저장 폴더가 이동되거나 삭제되지 않았는지 확인하세요.')
    if kind == 'tool_missing':
        return tr('영상 처리 도구를 찾지 못했습니다. 최신 앱의 ZIP 전체를 새 폴더에 풀고 다시 실행하세요.')
    if kind == 'launch':
        return tr('영상 처리 도구를 실행하지 못했습니다. Windows 10·11 x64용 앱의 ZIP 전체를 새 폴더에 풀고 다시 실행하세요.')
    if kind == 'io':
        return tr('영상을 읽거나 저장하지 못했습니다. 원본 파일·네트워크 연결과 저장 폴더 상태를 확인하세요. 자세한 내용은 Settings → 진단에서 확인하세요.')
    return tr('영상 처리 도구가 작업을 완료하지 못했습니다. Settings → 진단의 오류 내용을 확인하세요. 계속 실패하면 최신 앱으로 다시 시도하세요.')


class ToolError(RuntimeError):
    def __init__(self, detail='', returncode=None, *, kind=None):
        self.kind = kind or error_kind(detail)
        if self.kind == 'unknown' and returncode is not None and returncode & 0xffffffff in (0xc0000135, 0xc000007b):
            self.kind = 'launch'  # Missing DLL / invalid executable image on Windows.
        self.returncode = returncode
        detail = re.sub(r'https?://\S+', '[URL]', detail).strip()
        self.detail = detail if len(detail) <= 4000 else detail[:2000] + '\n…\n' + detail[-2000:]
        super().__init__(error_message(self.kind))
