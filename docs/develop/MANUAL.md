# 설명서 고치고 게시하기

설명서는 `docs/manual/manual.html` 한 파일로 열립니다. 같은 폴더의 `index.html`은 프로그램 소개 페이지이며 손으로 작성합니다. 한국어·영어 본문과 영어판 캡처를 함께 담습니다. `docs/manual` 폴더를 통째로 복사하면 브라우저에서 오프라인으로 읽을 수 있습니다. 앱의 **도움말**과 **F1**은 기본 브라우저로 같은 HTML을 엽니다.

## 본문 고치기

1. `docs/manual/content.json`에서 한국어와 영어 본문을 함께 고치세요.
2. 메뉴·버튼 이름은 `{{메시지 원문}}`으로 적으세요. 빌드할 때 현재 코드와 번역 파일에서 실제 이름으로 채웁니다. 이름을 직접 적으면 번역을 바꿔도 갱신되지 않습니다.
3. 빌드하고 검사하세요.

   ```sh
   uv run python run/build_manual.py
   uv run python run/check_manual.py
   node --check docs/manual/guide.js
   node run/check_manual_interactions.cjs
   ```

4. 브라우저에서 `docs/manual/manual.html`을 열어 사진 번호, 언어 전환, 이전·다음, 전체 보기, 인쇄를 확인하세요.

자동 검사는 두 언어의 항목 대응, 이미지와 앵커, 주석 위치, 좁은 화면의 번호 겹침, 단계 선택·강조 로직을 확인합니다. 실제 브라우저 렌더링과 인쇄 결과, 보조기기 동작은 4번에서 직접 확인해야 합니다.

## 문장 기준

사용자에게 보이는 본문에는 할 일, 선택의 결과, 문제 해결만 넣습니다. 독자 집단에 대한 홍보, 작성 의도, 구현 사정은 넣지 않습니다.

1. **할 일을 먼저 씁니다.** 행동과 실제 버튼 이름을 앞에 두고, 이유는 필요할 때 짧게 덧붙입니다.
2. **제목에 사용자가 하려는 일을 씁니다.** '시간 범위 지정', '저장할 클립 선택하기'처럼 씁니다.
3. **절차와 선택지를 구분합니다.** 순서대로 할 작업은 1·2·3, 대안 중 하나를 고르는 작업은 A·B·C로 표시합니다. '중 하나'와 '선택 사항'을 글로 함께 적어 문자만으로 뜻을 추측하게 하지 않습니다.
4. **설명을 찾는 조작을 줄입니다.** 전체 보기가 기본이고 본문을 접지 않습니다. 사진의 표식을 누르면 해당 설명의 배경만 강조합니다. 표식과 본문의 문자를 일치시킵니다.
5. **기술 설명은 선택의 결과로 바꿉니다.** 내부 수치보다 화질·용량·시간에 생기는 차이를 먼저 설명합니다. 고급 내용은 관련 작업 아래에 둡니다.
6. **기다릴 일을 미리 알립니다.** 최초 다운로드 용량과 예상 시간, 긴 영상의 분석 지연을 해당 작업에서 안내합니다. 추정치를 보장 시간처럼 쓰지 않습니다.
7. **표기 정책은 설명하지 않습니다.** 사용자가 눌러야 할 현재 메뉴 이름만 적습니다.
8. **두 언어를 각각 문맥에 맞게 씁니다.** 문장을 기계적으로 대응시키기보다 같은 행동과 결과를 안내합니다.

### 문장 수정 예시

| 피할 표현 | 사용할 표현 |
|---|---|
| 저장할 클립을 준비하세요. | 저장할 클립에 체크하세요. |
| 화질 값 18은 고화질, 23은 균형입니다. | 처음에는 '균형 있게'를 쓰세요. 더 선명한 영상을 원하면 '선명하게'를 선택하세요. |
| 느린 인코딩은 압축 효율이 높습니다. | 저장에 시간이 더 걸리지만, 비슷한 화질을 더 작은 파일에 담습니다. |
| 곡명·이름·달성률을 수정한 뒤 진행하세요. | 파일명이 괜찮다면 수정하지 않아도 됩니다. |

기준의 근거: [W3C 쉬운 단어 쓰기](https://www.w3.org/WAI/WCAG2/supplemental/patterns/o3p01-clear-words/), [W3C 단계 분리하기](https://www.w3.org/WAI/WCAG2/supplemental/patterns/o3p09-separated-instructions/), [GOV.UK 간결하고 능동적인 문장](https://guidance.publishing.service.gov.uk/writing-to-gov-uk-standards/writing-guidelines/clear-language/). 확인일 2026-09-16. W3C 문서는 WCAG 보충 지침입니다. 이 기준을 따랐다는 사실이 WCAG 준수나 특정 사용자 집단의 사용성을 뜻하지는 않습니다.

## 고친 뒤 확인

- 제목만 훑어도 필요한 작업을 찾을 수 있는가?
- 각 단계에서 무엇을 누르고 어떤 결과를 확인할지 알 수 있는가?
- 모든 방법을 해야 하는지, 하나만 고르면 되는지 분명한가?
- 선택 편집을 필수 절차처럼 쓰지 않았는가?
- 버튼 이름과 사진의 표식이 본문과 일치하는가?
- 짧게 쓰려다 순서·결과·대기 정보를 빼지 않았는가?

## GitHub Pages에 게시하기

저장소의 HTML 링크는 소스 보기로 열립니다. GitHub Pages를 쓰면 사진과 버튼이 동작합니다.

`.github/workflows/pages.yml`이 `docs/manual` 폴더를 올립니다. 저장소에서 **한 번만** 설정하세요.

1. **Settings → Pages → Source**에서 **GitHub Actions**를 고르세요.
2. `main`에 설명서나 번역을 밀면 워크플로가 실행됩니다. Actions 탭의 **Run workflow**로 직접 실행해도 됩니다.
3. **Settings → Pages → Visit site**에서 주소를 확인하세요.
4. README의 링크가 확인한 주소를 가리키는지 보세요. 현재 주소는 https://151net.github.io/ArcadeClip/ 입니다.

워크플로는 올리기 전에 `run/check_manual.py`와 Node 검사를 실행합니다. `content.json`이나 번역을 고치고 설명서를 다시 빌드하지 않았다면 여기서 멈춥니다. 두 검사 모두 표준 라이브러리만 쓰므로 의존성을 설치하지 않습니다.

기본 도메인을 쓰면 주소는 `https://151net.github.io/ArcadeClip/`입니다. 폴더 최상단의 `index.html`이 소개 페이지, `manual.html`이 설명서입니다. 설명서는 한국어 `#ko/overview/1`, 영어 `#en/overview/1`을 붙이면 해당 항목이 바로 열립니다.

참고: [GitHub Pages 소개](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages), [공식 워크플로 설정](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).

## 관련 문서

- [개발 환경 준비](SETUP.md)
- [Windows 배포본 만들기](BUILD.md)
