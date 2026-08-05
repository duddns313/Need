"""카테고리 체계 — 시골쥐식 3분법(고정비/변동비/저축투자) + 수입 + 제외.

모든 집계·차트·엑셀이 이 파일 하나를 참조한다.
카테고리를 늘리고 싶으면 여기만 고친다.
"""

# 성격(축)
INCOME = '수입'
FIXED = '고정비'
VARIABLE = '변동비'
SAVING = '저축투자'
EXCLUDED = '제외'

NATURES = [INCOME, FIXED, VARIABLE, SAVING, EXCLUDED]

# 집계에 실제로 잡히는 축 (제외는 빠진다)
COUNTED_NATURES = [INCOME, FIXED, VARIABLE, SAVING]

# 카테고리 -> 성격
CATEGORIES = {
    # 수입
    '급여': INCOME,
    '부수입': INCOME,
    '금융소득': INCOME,
    # 고정비
    '주거·관리비': FIXED,
    '통신': FIXED,
    '보험': FIXED,
    '구독료': FIXED,
    '대출이자': FIXED,
    # 변동비 — 먹는 돈을 넷으로 쪼갠다.
    # 장 봐서 해 먹는 것과 시켜 먹는 것은 성격이 완전히 다르고,
    # 줄이는 방법도 다르다. 뭉쳐 놓으면 어디를 손댈지 알 수 없다.
    '식료품': VARIABLE,        # 장보기 — 마트·청과·정육·편의점
    '외식': VARIABLE,          # 식당에서 먹은 것
    '배달': VARIABLE,          # 배민·요기요·쿠팡이츠
    '카페·간식': VARIABLE,     # 커피·디저트·베이커리
    '생활용품': VARIABLE,      # 먹는 것 아닌 생필품·미용·세탁
    '교통': VARIABLE,
    '의료·건강': VARIABLE,
    '반려동물': VARIABLE,     # 병원비가 커서 사람 의료비에 섞이면 둘 다 안 보인다
    '문화·여가': VARIABLE,
    '게임': VARIABLE,         # 문화·여가에 묻으면 얼마 쓰는지 안 보인다
    '쇼핑': VARIABLE,
    '경조·선물': VARIABLE,
    # 남에게 보낸 돈. 내 계좌 사이를 옮긴 것과 섞으면 '안 쓴 돈'으로 둔갑한다.
    # 실제 파일에서 594만원이 그렇게 사라져 있었다. 무엇에 쓴 건지는 모르지만
    # 집 밖으로 나간 것은 분명하니 일단 쓴 돈으로 세고, 화면에서 고치게 한다.
    '개인송금': VARIABLE,
    '여행·숙박': VARIABLE,
    '자동차': VARIABLE,
    '교육': VARIABLE,
    '세금·기타': VARIABLE,
    '미분류': VARIABLE,
    # 저축·투자
    # 연금저축·IRP·청약·ISA는 그냥 저축이 아니라 '세금을 깎아 주는 저축'이다.
    # 뭉뚱그리면 한도를 얼마나 썼는지 알 수 없어서 매년 환급을 놓친다.
    '연금저축': SAVING,
    'IRP·퇴직연금': SAVING,
    '주택청약': SAVING,
    'ISA': SAVING,
    '저축·예금': SAVING,
    '투자': SAVING,
    '대출원금상환': SAVING,
    # 제외 (지출이 아니다)
    '내계좌이체': EXCLUDED,
    '카드대금': EXCLUDED,
    '페이충전': EXCLUDED,
    '부부간이체': EXCLUDED,
    '대출실행': EXCLUDED,      # 대출을 받아 통장에 들어온 돈. 소득이 아니라 빚이다
    '환불·취소': EXCLUDED,     # 결제를 물러서 되돌아온 돈. 번 게 아니다
    '결제취소': EXCLUDED,      # 카드값이 나갔다 그대로 되돌아온 것. 짝이 맞는다
}

# 화면·엑셀에서의 표시 순서
ORDER = list(CATEGORIES.keys())


def nature_of(category):
    """카테고리의 성격을 돌려준다. 모르는 카테고리는 변동비로 본다."""
    return CATEGORIES.get(category, VARIABLE)


def categories_of(nature):
    """해당 성격에 속하는 카테고리 목록 (표시 순서 유지)."""
    return [c for c in ORDER if CATEGORIES[c] == nature]
