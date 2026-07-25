# (나중에 구현) 콘텐츠 자동 이미지 생성 기능

> 상태: **구현 완료(2026-07).** 본문=실제 데이터 차트(matplotlib), 대표=AI 컨셉(Pollinations), 둘 다 ALT 자동.
> 조사 결과 방향 수정: 범용 AI 이미지는 SEO 가치 낮음 → **본문은 진짜 데이터 차트**(E-E-A-T/이미지검색),
> **대표는 AI 컨셉**(썸네일/CTR용 장식). 고품질 유료 전환 시 seo_writer.py의 IMAGE_PROVIDER만 교체.

## 개요
Claude가 쓴 SEO 글에 AI 생성 이미지를 자동으로 붙여 워드프레스 초안에 넣는다.
Claude는 이미지 생성을 못 하므로 **별도 이미지 AI**를 하나 붙인다.

## 처리 흐름 (seo_writer.py에 얹기)
```
Claude가 글 작성 (현재)
  ↓
① Claude가 글 주제 기반 "영어 이미지 프롬프트" 2개 생성 (대표용 / 본문용)
    - 제약: 추상·컨셉 이미지만. 가짜 차트·숫자·특정종목 로고/그림 금지(오해·E-E-A-T 위험)
  ↓
② 이미지 AI로 이미지 2장 생성
  ↓
③ 워드프레스 미디어 라이브러리에 업로드 → media id 획득
  ↓
④ 대표 이미지 = featured_media 로 설정
   본문 이미지 = content_html 안에 <img> 삽입 (예: 첫 <h2> 앞)
```

## 이미지 AI provider (교체 가능하게 설계)
- **1차: Pollinations.ai (무료·키 불필요)**
  - `GET https://image.pollinations.ai/prompt/{URL인코딩된_프롬프트}?width=1200&height=630`
  - 응답 = 이미지 바이트. 가입/결제/시크릿 전부 불필요.
- **이후: 유료 고품질로 교체 (코드에서 provider 함수만 스위치)**
  - OpenAI `gpt-image-1` (images.generate) — `OPENAI_API_KEY` 필요, 장당 약 50~80원
  - 또는 Google Imagen (Gemini API) — `GEMINI_API_KEY` 필요

## 워드프레스 업로드 방법 (REST API)
- `POST {WP_URL}/wp-json/wp/v2/media`
  - Header: `Content-Disposition: attachment; filename="header.png"`, `Content-Type: image/png`
  - Auth: 기존 Basic Auth (`WP_USER` / `WP_APP_PASSWORD`) 재사용
  - 응답의 `id` = media id, `source_url` = 이미지 URL
- 글 저장/수정 시 `featured_media: <media id>` 로 대표 이미지 지정
- 본문 삽입은 `source_url`을 `<img>`로 content_html에 끼워넣기

## 코드 변경 지점
- `scripts/seo_writer.py`
  - 이미지 프롬프트 생성(Claude 짧은 호출) 함수
  - 이미지 생성 함수 (provider 스위치: pollinations / openai / google)
  - WP 미디어 업로드 함수
  - `_create_draft`에 `featured_media` 추가 + content_html에 본문 이미지 삽입
- `.github/workflows/seo_writer.yml`
  - (유료 전환 시에만) 이미지 API 키 env 추가

## 주의사항
- **보안 플러그인(NinjaFirewall 등)이 미디어 업로드를 막을 수 있음** → 실패해도 글 저장은 되게 graceful 처리
- **금융 콘텐츠 이미지 원칙**: 가짜 데이터/차트/로고 금지, 컨셉·분위기 이미지만
- 무료(Pollinations)는 품질·응답 안정성이 들쭉날쭉할 수 있음 → 실패 시 이미지 없이 글만 저장하도록 fallback
