# Windows 배포본 만들기

`dist/ArcadeClip.dist/` 폴더에 `ArcadeClip.exe`와 실행에 필요한 파일을 함께 만듭니다. 진단 로그를 남기며 실행하는 `ArcadeClip_dbg.cmd` 실행 파일도 같이 만듭니다. 배포할 때는 이 폴더 전체를 전달합니다.

## 만들기 전에

1. [개발 환경 준비](SETUP.md)를 끝내세요.
2. `tools/`에 FFmpeg 파일을 두세요. `uv run python run/fetch_tools.py`가 잠금 파일대로 받아 해시까지 확인합니다. [run/tools.lock.json](../../run/tools.lock.json)에 적힌 실행 파일과 DLL을 모두 확인하며, 해시가 다르면 빌드가 중단됩니다.
3. 이전 `dist/ArcadeClip.dist/` 폴더를 옮기거나 지우세요. 폴더가 남아 있으면 빌드가 중단됩니다.

## 만들기

```powershell
.\run\build_windows.ps1
```

Windows에서만 실행됩니다. 빌드에는 시간이 오래 걸립니다. 스크립트는 다음 순서로 진행하고, 한 단계라도 실패하면 멈춥니다.

1. 의존성 설치 (`uv sync --locked --group build`)
2. 번역 컴파일
3. 설명서 빌드와 검사
4. 고정 도구 해시 확인
5. 라이선스 수집
6. EXE 빌드
7. 번들에 들어간 자산·Qt 미디어 파일 확인

로그와 보고서는 `.validation-data/`에 남습니다.

## 따로 실행하는 명령

| 하고 싶은 일 | 명령 |
|---|---|
| 자동 검사 | `uv run python run/run_tests.py` |
| 자동 검사 · 화면 없이 | `uv run python run/run_tests.py --no-display` |
| 번역할 문자열 추출 | `uv run python run/extract_messages.py` |
| 번역 컴파일 | `uv run python run/compile_messages.py` |
| 설명서 빌드 | `uv run python run/build_manual.py` |
| 설명서 검사 | `uv run python run/check_manual.py` |
| 도구 해시 확인 | `uv run python run/verify_tools.py tools` |
| 라이선스 수집 | `uv run python run/collect_licenses.py --tools-directory tools` |

수집한 라이선스는 앱의 **Settings → 오픈소스 라이선스**에서 보입니다. 다른 운영체제로 배포한다면 해당 바이너리와 Python 런타임의 고지문을 그 환경에서 다시 수집해야 합니다.

## 태그를 달면 자동으로 빌드하기

`.github/workflows/release.yml`이 **`v`로 시작하는 태그를 밀면** Windows 배포본을 만들고 릴리스 초안에 올립니다. Actions 탭의 **Run workflow**로 직접 실행하면 빌드만 하고 결과물을 아티팩트로 남깁니다.

```sh
git tag v0.1.0
git push origin v0.1.0
```

태그 이름은 `src/updates.py`의 `VERSION`과 맞아야 합니다. 앱의 **새 버전 확인**이 릴리스 태그를 이 값과 비교하므로, 어긋나면 워크플로가 먼저 중단됩니다.

워크플로가 하는 일은 로컬 빌드와 같은 순서입니다. 다른 점은 셋입니다.

- `run/fetch_tools.py`로 `tools/`를 채웁니다. 저장소에 없는 폴더입니다.
- Nuitka 캐시를 `actions/cache`로 보존합니다. 없으면 매번 모든 모듈을 다시 컴파일합니다.
- 자동 검사를 `QT_QPA_PLATFORM=offscreen`으로 실행합니다.

릴리스는 **초안**으로 만들어집니다. 내용을 확인한 뒤 직접 공개하세요. 배포본을 올리면 GPLv3 제6조에 따라 FFmpeg 대응 소스도 제공해야 하며, 릴리스 본문에 해당 링크가 들어갑니다.

## FFmpeg 바꾸기

1. Gyan 릴리스의 **full_build-shared** ZIP을 내려받고 GitHub 릴리스 자산의 SHA-256과 대조하세요. 정적 full 빌드는 `ffmpeg.exe`와 `ffprobe.exe`가 같은 코덱을 각각 담아 400 MB를 넘습니다.
2. ZIP의 `bin/`에서 `ffplay.exe`를 뺀 9개 파일을 `tools/`에 복사하세요.
3. `run/tools.lock.json`의 버전·아카이브 해시와 파일별 해시를 갱신하세요. 빌드 스크립트는 이 목록을 그대로 번들에 복사합니다.
4. 라이선스 문서와 `-version`·`-L` 출력을 다시 수집하세요.
5. 자동 검사와 빌드를 다시 실행하세요.

PyAV와 Qt 안에 포함된 FFmpeg 라이브러리는 각 패키지에 속합니다. `tools/`의 외부 실행 파일 교체와는 별개입니다.

## 관련 문서

- [개발 환경 준비](SETUP.md)
- [설명서 고치고 게시하기](MANUAL.md)
