# 리빙(생활) + 쿠팡 파트너스 제휴 블로그 운영 가이드

기존 주식 파이프라인과 **병행**하는 리빙 리뷰 블로그 시스템입니다.
음식·패션·취미·TV 등 뜨는 키워드로 실사용 리뷰 글을 만들고, 글 하단에
쿠팡 파트너스 추천 링크를 넣어 수익화합니다.

## 전체 그림 (하이브리드)

```
키워드(+사진, +쿠팡상품)  ──►  scripts/living_draft.py (Claude 초안 생성)
                                        │
                     ┌──────────────────┴──────────────────┐
                     ▼                                      ▼
        [자체 사이트] site/_posts/*.md            [네이버] Notion '발행대기' DB
        GitHub Pages 자동 발행                    → 복붙 후 사진 넣고 수동 발행
        (제휴링크·자동화 자유, 유입 느림)         (유입 최강, 자동 API 없음)
```

- **네이버 블로그**: 검색 유입 최강. 단, 개인 블로그 글쓰기 API가 없어 **자동 발행 불가** →
  Notion에 만들어 둔 초안을 복붙하고 사진·링크를 손으로 넣어 발행. 제휴링크 과다 시
  저품질 위험이 있으니 글 1개당 링크는 1~2개, 정보성 본문 위주로.
- **자체 사이트**: `site/` = Jekyll(GitHub Pages 네이티브). 포스트를 커밋하면 자동 배포.

## 콘텐츠 엔진 (작가↔검수 에이전트 루프)

글은 **작가 에이전트**가 쓰고 **검수 에이전트**가 SEO·품질을 평가해 수정 가이드를 주면
작가가 다시 쓰는 과정을 반복해 완성합니다. **생성과 발행은 분리**되어 있어 초안은 절대
자동으로 나가지 않고, 항상 최종본을 사람이 확인한 뒤 발행합니다.

### 3단계 흐름

```bash
# 1) 생성 — 작가→검수 반복 후 drafts/ 에 저장(발행 안 함)
python -m scripts.living_draft --keyword "에어프라이어 추천" --type 실사용후기 \
  --tone seo --photos ~/pics/af --product "에어프라이어" --max-rounds 2

# 2) (선택) 사람 피드백으로 수동 재작성
python -m scripts.living_draft --revise drafts/2026-07-18-airfryer.json \
  --feedback "도입부 더 짧게, 가격대 정보 추가"

# 3) 최종 컨펌 후 발행
python -m scripts.living_draft --publish-draft drafts/2026-07-18-airfryer.json --to site
```

생성 옵션:
- `--keyword` (필수): 핵심 주제/키워드
- `--type`: `실사용후기` | `장점` | `단점` | `상품소개` | `일상일기` (유형별 글 구조가 달라짐)
- `--product`: 쿠팡 상품 URL 또는 검색어. URL이면 추천링크만, 검색어면 대표이미지+링크 조회
- `--photos`: 사진 폴더 → `site/assets/photos/<slug>/`로 복사하고 본문에 자동 배치
- `--notes`: 실사용 메모(텍스트 또는 파일 경로) → 글에 반영
- `--tone`: `seo`(검색 최적 존댓말) 또는 `my`(내 말투 흉내)
- `--voice-sample`: `my`일 때 내 글 샘플 파일(few-shot)
- `--max-rounds`(기본 2), `--pass-score`(기본 80): 검수 반복 횟수와 통과 점수
- 환경변수 `LIVING_MODEL`: 글 품질을 높이려면 `claude-opus-4-8` 등으로 교체

발행 옵션: `--publish-draft <draft.json> --to site|notion|both`

### 에이전트 고도화("학습") — `style/`
코드를 안 고치고도 품질을 키웁니다(자세히는 `style/README.md`):
- `style/rubric.md` — 검수 기준 체크리스트(고치면 검수 방향이 바뀜)
- `style/examples/` — 마음에 든 글/‘수정 전→후’ 예시를 넣으면 작가에 few-shot으로 주입
- `scripts/agents/prompts/*.md` — 작가·검수 시스템 프롬프트, 유형별 지침(편집형)
- 운영: 초반 `--max-rounds 1`로 보며 손보고 예시를 쌓다가, 성숙하면
  `--max-rounds 3 --pass-score 85`로 무인 반복 → 최종본만 확인·발행.

### GitHub Actions
`.github/workflows/living_publish.yml`에서 **Run workflow**로 키워드·유형 실행(수동).
> 참고: 워크플로는 아직 1차(한방 생성) 흐름 기준입니다. 위 3단계(생성→컨펌 발행)에 맞춘
> 워크플로 개편은 Track 1(급상승 자동화) 착수 때 함께 진행합니다.

## GitHub Secrets

| Secret | 용도 | 필수 |
|---|---|---|
| `ANTHROPIC_API_KEY` | Claude 글 생성 | ✅ |
| `COUPANG_ACCESS_KEY` / `COUPANG_SECRET_KEY` | 쿠팡 파트너스 링크·대표이미지 | 링크 넣을 때 |
| `NOTION_TOKEN` / `NOTION_DB_LIVING` | 네이버용 초안 저장 DB | `notion`/`both` |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | 완료 알림 | 선택 |

- `NOTION_DB_LIVING`: Notion에 '리빙 발행대기' DB를 새로 만들고 그 DB ID를 넣으세요
  (기존 주식용 `NOTION_DB_A~D`와 별개).
- 쿠팡 키 미설정 시: 링크 없이 초안만 생성되고, 수수료를 안 받으므로 고지문구도 생략됩니다.
  승인 후 키를 넣으면 링크+고지문구가 자동으로 붙습니다.

## 쿠팡 파트너스 준비

1. partners.coupang.com 가입·승인 → 트래킹 코드 발급.
2. Open API 사용 신청 → ACCESS/SECRET KEY 발급 후 위 Secrets에 등록.
3. **고지문구는 법적 필수**입니다(공정위 표시광고 + 쿠팡 약관). 링크가 있으면 코드가
   자동으로 넣어주지만, 네이버 수동 발행 시엔 반드시 직접 확인하세요:
   > 이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다.

## 이미지 정책 (중요)

- **① 직접 촬영이 항상 최선** — 원본 사진은 SEO·신뢰도 최고이며, 특히 네이버는 원본을 우대합니다.
  `--photos` 폴더에 넣으면 자동 삽입됩니다.
- **② 쿠팡 대표 이미지** — 못 찍는 제품은 파트너스 API가 주는 **상품 대표 이미지**만 사용 가능
  (그 상품 홍보 목적). `--product` 검색어로 넣으면 자동 취득됩니다.
- **❌ 판매자 상세페이지 사진 임의 사용 금지** — 쿠팡에 있다고 무료가 아니며 저작권은
  판매자/제조사에 있습니다. 긁어 쓰지 마세요.
- **AI 생성 이미지**는 썸네일·설명용 보조로만(Phase 2), '실사용 후기 사진'으로 쓰지 마세요.

## 저품질/정책 회피 수칙

- 링크만 있고 알맹이 없는 얇은 글 금지 → 실제 정보·경험을 충분히.
- 글 1개당 제휴 링크 1~2개로 절제(특히 네이버).
- 같은 문장·이미지 재탕 금지, 제목·본문 키워드는 자연스럽게.

## GitHub Pages 켜기 (자체 사이트)

repo Settings → Pages → Source: **Branch `main`, Folder `/site`** 선택.
`site/_config.yml`의 `url`을 배포 주소(예: `https://<계정>.github.io`)로 바꾸면
`jekyll-seo-tag`가 메타/OG를, `jekyll-sitemap`이 `sitemap.xml`을 자동 생성합니다.
로컬 미리보기: `cd site && bundle install && bundle exec jekyll serve`.

## 파일 맵

| 경로 | 역할 |
|---|---|
| `scripts/living_draft.py` | 오케스트레이터(작가↔검수 루프·스테이징·발행) 엔트리포인트 |
| `scripts/agents/writer.py` | 작가 에이전트(초안 작성/재작성) |
| `scripts/agents/reviewer.py` | 검수 에이전트(SEO·품질 평가 → 수정 가이드) |
| `scripts/agents/prompts/*.md` | 작가·검수 프롬프트, 유형별 지침(편집형) |
| `style/` | 학습 자산(검수 루브릭 + few-shot 예시) |
| `scripts/common/coupang.py` | 쿠팡 파트너스 API(deeplink·대표이미지) |
| `scripts/common/affiliate.py` | 제휴 링크 블록 + 고지문구 |
| `drafts/` | 초안 스테이징(발행 전 임시본, git 미추적) |
| `site/` | Jekyll 정적 블로그(GitHub Pages) |
| `.github/workflows/living_publish.yml` | 수동 발행 워크플로(1차 흐름) |

주식 파이프라인(`pykrx_scan.py`·`dart_scan.py`·`x_scan.py`·`analyze.py`)은 건드리지 않았습니다.
