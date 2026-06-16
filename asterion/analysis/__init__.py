"""Analysis 계층 — 품질 평가(Sentinel)·전처리(Forge)·보정(Calibration).

로드맵 §10. 초기에는 ML 없이 '구조를 연결할 자리'만 만든다: Sentinel이 기본
품질 지표로 accepted/warning/rejected를 판정하는 인터페이스, 그리고 프레임
픽셀 데이터(히스토그램/라인프로파일/통계) API. FWHM·star count·자동 보정 등
무거운 분석은 이 인터페이스 뒤에 나중에 끼운다.
"""
