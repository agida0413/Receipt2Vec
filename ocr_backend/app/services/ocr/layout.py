"""
OCR 결과(텍스트 박스)를 사람이 읽는 '줄(row)' 단위로 묶는다.

PaddleOCR 는 박스 단위로 결과를 주기 때문에, 영수증의 "상품명 ... 수량 ... 금액" 처럼
같은 줄에 떨어져 있는 텍스트가 별개 박스로 나온다. 세로 중심이 가까운 박스를 한 줄로 묶고
왼쪽→오른쪽 순서로 이어 붙여야 파서가 줄 단위로 해석할 수 있다.
"""
from dataclasses import dataclass, field
from typing import List, Tuple


@dataclass
class OcrLine:
    text: str
    score: float
    box: Tuple[int, int, int, int]  # x1, y1, x2, y2

    @property
    def cx(self) -> float:
        return (self.box[0] + self.box[2]) / 2

    @property
    def cy(self) -> float:
        return (self.box[1] + self.box[3]) / 2

    @property
    def height(self) -> float:
        return max(1, self.box[3] - self.box[1])


@dataclass
class Row:
    lines: List[OcrLine] = field(default_factory=list)

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


def group_rows(lines: List[OcrLine], tolerance: float = 0.5) -> List[Row]:
    """세로 중심 차이가 (박스 높이 * tolerance) 이내인 박스들을 한 줄로 묶는다."""
    rows: List[Row] = []
    for line in sorted(lines, key=lambda l: (l.cy, l.box[0])):
        if rows:
            last = rows[-1]
            limit = max(line.height, last.height) * tolerance
            if abs(line.cy - last.cy) <= limit:
                last.lines.append(line)
                continue
        rows.append(Row(lines=[line]))
    for row in rows:
        row.lines.sort(key=lambda l: l.box[0])
    return rows
