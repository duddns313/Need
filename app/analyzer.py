"""
문서 분석.

이 앱은 문서에 등장하는 '모든' 법령을 찾지 않습니다. 요구사항 관리카드(엑셀)와
부서별 요구사항 변경 관리카드(한글)에 실제로 등록된 항목만 관리 대상으로
취급합니다. 두 문서 모두 표 형식이 정해져 있어서, 그 구조를 그대로 읽으면
됩니다 — 문서 전체를 정규식으로 훑어 잡음 섞인 후보를 만드는 방식은 쓰지
않습니다.

  extract_from_cards  : 엑셀 "요구사항(총괄)" 표에서 관리 대상 목록을 만듭니다.
  enrich_with_domain  : 부서별 한글 카드에서 관리번호·관련사규·반영계획을 보탭니다.
  evaluate             : 카드에 이미 적힌 '개정 필요여부'를 기준으로 판정합니다.
  compare_ai            : (선택) Claude가 내부규정 발췌본을 직접 읽고 판정합니다.

AI 모드에는 Claude Console(console.anthropic.com)에서 발급한 API 키가 필요합니다.
Claude Pro/Max 구독 계정과는 별개의 결제 체계라, 구독 아이디로는 동작하지 않습니다.
"""
from __future__ import annotations
import json
import re
from dataclasses import dataclass, asdict, field

DEFAULT_MODEL = 'claude-sonnet-5'


@dataclass
class Reference:
    """요구사항 관리카드에 등록된 관리 대상 1건(법령/행정규칙)."""
    name: str
    articles: list[str] = field(default_factory=list)
    source_files: list[str] = field(default_factory=list)
    context: str = ''
    confidence: str = 'high'
    code: str = ''
    dept: str = ''
    owner_dept: str = ''
    card_effective: str = ''
    card_promulgation: str = ''
    revision_change: str = ''
    revision_needed: bool = False
    related_internal: list[str] = field(default_factory=list)
    recommendation: str = ''
    due_date: str = ''

    def to_dict(self) -> dict:
        return asdict(self)


def norm_name(s):
    """공백·구두점을 지워 이름 비교를 쉽게 만듭니다."""
    return re.sub('[^\\w가-힣]', '', s or '')


def strip_parens(s):
    return re.sub('[\\(（][^)）]*[\\)）]', '', s or '').strip()


def split_names(s):
    """쉼표로 이름을 나누되, 괄호 안의 쉼표(조문 목록 등)는 무시합니다."""
    parts = []
    depth = 0
    buf = []
    for ch in s or '':
        if ch in '([（【':
            depth += 1
        elif ch in ')]）】':
            depth = max(0, depth - 1)
        if ch in ',、' and depth == 0:
            parts.append(''.join(buf))
            buf = []
            continue
        buf.append(ch)
    if buf:
        parts.append(''.join(buf))
    return [p.strip() for p in parts if p.strip() and p.strip() not in ('-', '—')]


def _find_col(header, cond):
    for i, cell in enumerate(header):
        flat = (cell or '').replace(' ', '').replace('\n', '')
        if cond(flat):
            return i
    return None


def extract_from_cards(tables):
    """요구사항 관리카드(엑셀)의 표에서 관리 대상 항목만 뽑아냅니다."""
    refs = []
    for table in tables:
        rows = table.get('rows', [])
        header_idx, header = None, None
        for i, row in enumerate(rows):
            flat = [(c or '').replace(' ', '').replace('\n', '') for c in row]
            if not any('요구사항' in c and '명' in c for c in flat):
                continue
            header_idx, header = i, row
            break
        if header is None:
            continue

        col_name = _find_col(header, lambda c: '요구사항' in c and '명' in c)
        col_change = _find_col(header, lambda c: '개정사항' in c)
        col_eff = _find_col(header, lambda c: '시행일' in c)
        col_need = _find_col(header, lambda c: '개정' in c and '필요' in c)
        col_related = _find_col(header, lambda c: '운영절차' in c) or _find_col(header, lambda c: '관련' in c and '규정' in c)
        col_dept = _find_col(header, lambda c: '관련부서' in c)
        col_owner = _find_col(header, lambda c: '담당부서' in c)
        col_due = _find_col(header, lambda c: '개정' in c and ('예정' in c or '완료' in c))
        col_reco = _find_col(header, lambda c: '주요' in c and '개정내용' in c)
        if col_name is None:
            continue

        def cell(row: list, idx: int | None) -> str:
            if idx is None or idx >= len(row) or row[idx] is None:
                return ''
            return str(row[idx]).strip()

        for row in rows[header_idx + 1:]:
            raw_name = cell(row, col_name)
            if not raw_name or raw_name in ('-', '—'):
                continue
            names = split_names(raw_name) or [raw_name]
            need_raw = cell(row, col_need)
            neg = {'', '-', 'N', 'X', 'n', 'x', '—', '무', '불필요', '해당없음', '해당 없음'}
            for name in names:
                refs.append(Reference(
                    name=name,
                    source_files=[table.get('sheet', '')],
                    dept=cell(row, col_dept),
                    owner_dept=cell(row, col_owner),
                    card_effective=cell(row, col_eff),
                    revision_change=cell(row, col_change),
                    revision_needed=need_raw.strip() not in neg,
                    related_internal=split_names(cell(row, col_related)),
                    recommendation=cell(row, col_reco),
                    due_date=cell(row, col_due),
                ))
    return refs


_ITEM_SPLIT_RE = re.compile('NO\\s*요구사항\\s*명\\s*최종개정일(?:\\(공포\\))?', re.S)
_CODE_LINE_RE = re.compile('^[가-힣A-Za-z]+[-–]\\s*\\d+$')


def _extract_after_label(text, label):
    lines = text.splitlines()
    for i, l in enumerate(lines):
        if l.strip() != label:
            continue
        for j in range(i + 1, len(lines)):
            if not lines[j].strip():
                continue
            return lines[j].strip()
        return ''
    return ''


def _extract_section(text, start_label):
    collecting = False
    out = []
    lines = text.splitlines()
    for l in lines:
        s = l.strip()
        if not collecting:
            if s.startswith('□') and start_label in s:
                collecting = True
            continue
        if s.startswith('□'):
            break
        if not s:
            continue
        out.append(s)
    joined = '\n'.join(out).strip()
    if joined in ('○ 해당없음', '○ 해당 없음', '해당없음', '해당 없음'):
        return ''
    return joined


_PROM_LINE_RE = re.compile('제[\\d\\-]+\\s*호')


def split_domain_card(text):
    """부서별 요구사항 변경 관리카드(hwp/hwpx)에서 항목 블록을 나눕니다."""
    chunks = _ITEM_SPLIT_RE.split(text)[1:]
    items = []
    for chunk in chunks:
        lines = [l.strip() for l in chunk.splitlines() if l.strip()]
        if not lines:
            continue
        code = lines[0] if _CODE_LINE_RE.match(lines[0]) else ''
        name_idx = 1 if code else 0
        if name_idx >= len(lines):
            continue
        prom_idx = name_idx + 1
        promulgation = lines[prom_idx] if prom_idx < len(lines) and _PROM_LINE_RE.search(lines[prom_idx]) else ''
        items.append({
            'code': code,
            'name': lines[name_idx],
            'promulgation': promulgation,
            'related_internal': split_names(_extract_after_label(chunk, '관련사규')),
            'revision_change': _extract_section(chunk, '요구사항 개정사항'),
            'recommendation': _extract_section(chunk, '관련규정 반영계획'),
        })
    return items


def build_domain_index(domain_docs):
    index = {}
    for _filename, text in domain_docs:
        for item in split_domain_card(text):
            key = norm_name(item['name'])
            if not key:
                continue
            entry = index.setdefault(key, {
                'codes': [],
                'related_internal': [],
                'revision_change': '',
                'recommendation': '',
                'promulgation': '',
            })
            if item['code'] and item['code'] not in entry['codes']:
                entry['codes'].append(item['code'])
            for r in item['related_internal']:
                if r not in entry['related_internal']:
                    entry['related_internal'].append(r)
            if item['revision_change'] and not entry['revision_change']:
                entry['revision_change'] = item['revision_change']
            if item['recommendation'] and not entry['recommendation']:
                entry['recommendation'] = item['recommendation']
            if item['promulgation'] and not entry['promulgation']:
                entry['promulgation'] = item['promulgation']
    return index


def enrich_with_domain(refs, domain_index):
    """
    엑셀에서 뽑은 항목에 부서별 한글 카드의 상세를 보탭니다.
    엑셀 각 행은 이미 그 행(부서)에 맞는 값을 갖고 있으므로, 엑셀 값이 있으면
    그대로 두고 비어 있는 항목만 한글 카드로 채웁니다. 여러 부서가 같은
    법령을 관리하는 한글 카드는 하나로 묶여 있어서, 무조건 합치면 부서
    구분 없이 관련 내규가 뒤섞입니다.
    """
    for ref in refs:
        key = norm_name(ref.name)
        if not key:
            continue
        match = domain_index.get(key)
        if not match:
            for k, v in domain_index.items():
                if key in k or k in key:
                    match = v
                    break
        if not match:
            continue
        if match['codes'] and not ref.code:
            ref.code = match['codes'][0]
        if not ref.related_internal and match['related_internal']:
            ref.related_internal = list(match['related_internal'])
        if not ref.revision_change and match['revision_change']:
            ref.revision_change = match['revision_change']
        if not ref.recommendation and match['recommendation']:
            ref.recommendation = match['recommendation']
        if not ref.card_promulgation and match['promulgation']:
            ref.card_promulgation = match['promulgation']


_CITE_RE = re.compile('[「『]\\s*([^」』\\n]{2,40}?)\\s*[」』]')
_CITE_SUFFIXES = ('법', '법률', '시행령', '시행규칙', '규칙', '기준', '고시', '훈령', '예규')
_CITE_EXCLUDE = re.compile('^(별지|별표|서식|참고|절차서|세칙)')


def find_uncarded_citations(internal_docs, carded_names):
    """내규에서 「」로 인용됐지만 관리카드에는 없는 법령명 후보를 찾습니다."""
    carded_keys = [norm_name(n) for n in carded_names]

    def already_carded(name: str) -> bool:
        key = norm_name(name)
        return any(key in ck or ck in key for ck in carded_keys)

    found = {}
    for filename, text in internal_docs:
        for m in _CITE_RE.finditer(text):
            name = re.sub('\\s+', ' ', m.group(1)).strip()
            if len(name) < 4 or _CITE_EXCLUDE.match(name):
                continue
            if not name.endswith(_CITE_SUFFIXES):
                continue
            if already_carded(name):
                continue
            found.setdefault(name, [])
            if filename not in found[name]:
                found[name].append(filename)
    return [{'name': n, 'source_files': files} for n, files in sorted(found.items())]


def _card_promulgation_numbers(s):
    """카드 텍스트에서 '제21188호'류 공포·발령번호 숫자만 뽑습니다."""
    return [re.sub('\\D', '', n) for n in re.findall('제\\s*([\\d\\-]+)\\s*호', s or '')]


def evaluate(record, ref, pending=None):
    """
    부서가 카드에 이미 적어둔 '개정 필요여부'를 신뢰하되, 그 판단 자체가
    최신 법령을 보고 내려진 것인지 먼저 확인합니다 — 카드에 적힌 공포·발령
    번호가 한글 카드에서 잡혔다면, 법령정보센터의 현재 공포·발령번호와
    대조합니다. 번호가 다르면 카드가 그 사이에 있었던 개정을 반영하지
    못했을 가능성이 높으므로, '개정 필요여부' 값과 무관하게 재확인을
    요청합니다. 이때 "확인해보세요"로 끝내지 않고, 무엇이 바뀌었는지
    (제개정구분·공포일)와 시행 예정인 추가 개정(pending)까지 구체적으로
    적고, 어느 내규를 봐야 하는지 짚어줍니다. (조문 단위 자동 비교 자체는
    하지 않습니다 — README 참고. record.raw/pending 정보로 사람이 마지막
    판단을 하도록 재료를 최대한 갖춰주는 것이 목표입니다.)

    카드의 '시행일' 칸은 종종 여러 조문의 서로 다른(미래) 시행일이 자유
    텍스트로 섞여 있어서(예: "시행1 '26.12.17. / 시행2 '27.06.17."),
    법령정보센터의 단일 시행일자와 기계적으로 비교하면 거꾸로 된 판정이
    나올 수 있습니다. 그래서 이 값끼리는 비교하지 않습니다 — '직전 실행
    대비 개정 발생' 여부는 법령정보센터 스냅샷끼리 비교하는 별도 로직
    (storage.compare_with_snapshot)이 더 안전하게 담당합니다.
    """
    if ref.card_promulgation and record.promulgation_no:
        card_nos = _card_promulgation_numbers(ref.card_promulgation)
        rec_no = re.sub('\\D', '', record.promulgation_no)
        if card_nos and rec_no not in card_nos:
            related = ', '.join(ref.related_internal) if ref.related_internal else '관련 내규'
            pending_txt = ''
            if pending:
                stages = '; '.join(
                    f"{p.get('effective', '-')} 시행 예정(공포 {p.get('promulgated', '-')}, 제{p.get('promulgation_no', '-')}호)"
                    for p in pending
                )
                pending_txt = f' 앞으로 시행될 개정도 있습니다 — {stages}.'
            return {
                'status': 'review',
                'headline': '카드 자체가 현행화되지 않았을 수 있음',
                'detail': f"카드에 적힌 공포·발령번호({ref.card_promulgation})와 법령정보센터의 현재 번호(제{record.promulgation_no}호, {record.promulgated} {record.revision or '개정'}, 시행 {record.effective})가 다릅니다 — 카드 작성 이후 개정이 있었던 것으로 보입니다.{pending_txt}",
                'action': f'위 개정 내용이 {related}에 반영되어 있는지 대조해주세요.',
            }
    if ref.revision_needed:
        related = ', '.join(ref.related_internal) if ref.related_internal else '관련 내규'
        return {
            'status': 'outdated',
            'headline': '개정 반영 필요 (부서 검토 결과)',
            'detail': ref.revision_change or '관리카드에서 개정 필요로 표시한 항목입니다.',
            'action': ref.recommendation or f'{related}에 반영 여부를 확인해주세요.',
        }
    return {
        'status': 'ok',
        'headline': '현행 일치 (부서 확인 완료)',
        'detail': '관리카드에서 개정 반영이 불필요하다고 확인된 항목입니다.',
        'action': '',
    }


_COMPARE_PROMPT = '''당신은 철도안전관리체계 내부규정의 현행화 상태를 점검합니다.

[대상 법령/행정규칙]
명칭: {name}
구분: {target_label}
소관: {ministry}
공포(발령)일자: {promulgated}
시행일자: {effective}
제개정구분: {revision}

[요구사항 관리카드에 부서가 이미 기재한 내용]
개정사항: {revision_change}
관련 내규: {related_internal}
개정 필요여부: {revision_needed}
카드의 반영 권고: {recommendation}

[사내 내부규정·안전관리체계 문서에서 발췌한 관련 부분]
{internal}

판단할 것:
1. 카드의 '개정 필요여부' 판단이 내부규정 발췌본과 비교했을 때 타당한가?
2. 내부 문서가 이 법령/행정규칙을 인용하고 있다면, 인용된 버전(개정일·시행일·조문번호)이
   위 최신 정보와 일치하는가?
3. 내부 문서에 아예 언급이 없다면 누락인가, 아니면 해당 없음인가?

반드시 아래 JSON 객체만 출력하세요. 설명이나 코드펜스 금지.
{{
  "status": "ok" | "review" | "outdated" | "missing",
  "headline": "한 줄 요약 (한국어, 40자 이내)",
  "detail": "판단 근거 (한국어, 3문장 이내)",
  "action": "담당자가 취할 조치 (한국어, 1문장). 조치 불필요하면 빈 문자열"
}}
'''
_MAX_INTERNAL = 20000


class AiUnavailable(RuntimeError):
    pass


def _client(api_key):
    if not api_key:
        raise AiUnavailable("Claude API 키가 등록되어 있지 않습니다. console.anthropic.com 에서 발급한 키를 설정 화면에 입력하거나, '카드 기준 판정' 모드로 전환해주세요.")
    try:
        from anthropic import Anthropic
        return Anthropic(api_key=api_key)
    except ImportError as e:
        raise AiUnavailable('anthropic 패키지가 설치되어 있지 않습니다.') from e


def _ask(api_key, model, prompt, max_tokens=1200):
    msg = _client(api_key).messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{'role': 'user', 'content': prompt}],
    )
    return ''.join(b.text for b in msg.content if getattr(b, 'type', '') == 'text')


def _parse_json(text):
    cleaned = re.sub('^```(?:json)?|```$', '', text.strip(), flags=re.M).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        m = re.search('[\\[{].*[\\]}]', cleaned, flags=re.S)
        if not m:
            raise
        return json.loads(m.group(0))


def compare_ai(record, ref, internal_text, api_key, model=DEFAULT_MODEL):
    """record: lawapi.LawRecord. ref: 카드에서 뽑은 관리 대상 항목."""
    excerpt = relevant_excerpt(internal_text, record.name)[:_MAX_INTERNAL]
    prompt = _COMPARE_PROMPT.format(
        name=record.name,
        target_label=record.target_label,
        ministry=record.ministry or '-',
        promulgated=record.promulgated or '-',
        effective=record.effective or '-',
        revision=record.revision or '-',
        revision_change=ref.revision_change or '-',
        related_internal=', '.join(ref.related_internal) or '-',
        revision_needed='필요' if ref.revision_needed else '불필요',
        recommendation=ref.recommendation or '-',
        internal=excerpt or '(내부 문서에서 관련 언급을 찾지 못했습니다.)',
    )
    try:
        return _parse_json(_ask(api_key, model, prompt))
    except Exception as e:
        return {
            'status': 'review',
            'headline': '자동 판정 실패',
            'detail': f'AI 응답을 해석하지 못했습니다: {e}',
            'action': '수동으로 확인해주세요.',
        }


def relevant_excerpt(text, name, window=1500):
    """내부 문서에서 해당 법령명 주변만 잘라냅니다(토큰 절약)."""
    flat_key = name.replace(' ', '')
    chunks = []
    start = 0
    lower = text.replace(' ', '')
    offset_map = []
    for i, ch in enumerate(text):
        if ch.isspace():
            continue
        offset_map.append(i)
    while True:
        idx = lower.find(flat_key, start)
        if idx == -1 or len(chunks) >= 5:
            break
        orig = offset_map[idx] if idx < len(offset_map) else 0
        chunks.append(text[max(0, orig - window // 2):orig + window // 2])
        start = idx + len(flat_key)
    return '\n…\n'.join(chunks)
