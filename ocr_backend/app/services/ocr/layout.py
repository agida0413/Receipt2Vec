"""
OCR 결과(텍스트 박스)를 사람이 읽는 '줄(row)' 단위로 묶는다.

PaddleOCR 는 박스 단위로 결과를 주기 때문에, 영수증의 "상품명 ... 수량 ... 금액" 처럼
같은 줄에 떨어져 있는 텍스트가 별개 박스로 나온다. 같은 줄의 박스를 묶어
왼쪽→오른쪽 순서로 이어 붙여야 파서가 줄 단위로 해석할 수 있다.

기울어진 사진 대응
  폰으로 찍은 영수증은 몇 도만 기울어도 왼쪽 상품명과 오른쪽 금액의 높이가 달라져서
  '세로 중심이 가까운 박스끼리' 묶으면 한 줄씩 밀린다 (실측: 4° 기울기 → '말보로 레드' 에 윗줄 금액이 붙음).
  → 글자 영역(4점)의 기울기 중앙값으로 사진 기울기를 추정하고, 그만큼 되돌린 높이(y − 기울기·x)로 묶는다.
"""
import math
from dataclasses import dataclass, field
from statistics import median
from typing import List, Optional, Tuple

Point = Tuple[float, float]


@dataclass
class OcrLine:
    text: str
    score: float
    box: Tuple[int, int, int, int]  # x1, y1, x2, y2 (축 정렬 외곽 박스)
    poly: Optional[Tuple[Point, Point, Point, Point]] = None  # 좌상, 우상, 우하, 좌하 (기울어진 글자 영역)

    @property
    def cx(self) -> float:
        return (self.box[0] + self.box[2]) / 2

    @property
    def cy(self) -> float:
        return (self.box[1] + self.box[3]) / 2

    @property
    def height(self) -> float:
        """글자 높이. 기울어진 박스는 외곽 박스가 부풀어 있으므로 4점 영역의 좌·우 변 길이를 쓴다"""
        if self.poly:
            (x0, y0), (x1, y1), (x2, y2), (x3, y3) = self.poly
            left, right = math.hypot(x3 - x0, y3 - y0), math.hypot(x2 - x1, y2 - y1)
            return max(1.0, (left + right) / 2)
        return max(1, self.box[3] - self.box[1])

    @property
    def slope(self) -> Optional[float]:
        """글자 줄의 기울기 (윗변 기준). 너무 짧은 박스는 신뢰도가 낮아 제외"""
        if not self.poly:
            return None
        (x0, y0), (x1, y1) = self.poly[0], self.poly[1]
        if x1 - x0 < max(40.0, self.height * 2):
            return None
        return (y1 - y0) / (x1 - x0)


@dataclass
class Row:
    lines: List[OcrLine] = field(default_factory=list)
    ys: List[float] = field(default_factory=list)  # 기울기 보정한 세로 위치

    @property
    def y(self) -> float:
        return sum(self.ys) / len(self.ys)

    @property
    def cy(self) -> float:
        return sum(l.cy for l in self.lines) / len(self.lines)

    @property
    def height(self) -> float:
        return sum(l.height for l in self.lines) / len(self.lines)

    @property
    def text(self) -> str:
        return " ".join(l.text for l in sorted(self.lines, key=lambda l: l.box[0]))

    @property
    def box(self) -> Tuple[int, int, int, int]:
        return (
            min(l.box[0] for l in self.lines),
            min(l.box[1] for l in self.lines),
            max(l.box[2] for l in self.lines),
            max(l.box[3] for l in self.lines),
        )


def estimate_skew(lines: List[OcrLine]) -> float:
    """박스 기울기 중앙값 (tan θ). 근거가 부족하거나 비정상적으로 크면 0"""
    slopes = [s for s in (l.slope for l in lines) if s is not None]
    if len(slopes) < 3:
        return 0.0
    s = median(slopes)
    return s if abs(s) <= 0.35 else 0.0  # ±19° 이상은 문서 방향 보정 영역 (잘못된 추정 방지)


def group_rows(lines: List[OcrLine], tolerance: float = 0.5) -> List[Row]:
    """
    기울기를 보정한 세로 위치가 (글자 높이 * tolerance) 이내인 박스들을 한 줄로 묶는다.
    직전 줄 하나와만 비교하지 않고 최근 몇 줄 중 가장 가까운 줄에 붙여서, 줄 간격이 좁아도 엇갈리지 않게 한다.
    """
    if not lines:
        return []
    skew = estimate_skew(lines)
    x_ref = min(l.box[0] for l in lines)
    deskew = lambda l: l.cy - skew * (l.cx - x_ref)  # noqa: E731

    rows: List[Row] = []
    for line in sorted(lines, key=lambda l: (deskew(l), l.box[0])):
        y = deskew(line)
        best, best_d = None, None
        for row in rows[-3:]:
            d = abs(y - row.y)
            if d <= max(line.height, row.height) * tolerance and (best_d is None or d < best_d):
                best, best_d = row, d
        if best is not None:
            best.lines.append(line)
            best.ys.append(y)
        else:
            rows.append(Row(lines=[line], ys=[y]))
    for row in rows:
        row.lines.sort(key=lambda l: l.box[0])
    return rows
