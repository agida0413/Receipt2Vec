"""
PaddleOCR(3.x, PP-OCRv5) 래퍼

- 모델은 프로세스당 1회만 로딩 (서버 시작 시 warmup)
- PaddleOCR 파이프라인은 스레드 안전하지 않으므로 Lock 으로 직렬화
"""
import logging
import math
import threading
from typing import List, Optional, Tuple

import numpy as np

from app.core.config import settings
from app.services.ocr.layout import OcrLine, estimate_skew

logger = logging.getLogger(__name__)


class OcrEngine:
    def __init__(self):
        self._ocr = None
        self._lock = threading.Lock()
        self._load_error: Optional[str] = None

    @property
    def is_ready(self) -> bool:
        return self._ocr is not None

    @property
    def load_error(self) -> Optional[str]:
        return self._load_error

    def warmup(self) -> None:
        try:
            self._get_ocr()
        except Exception as e:  # 서버는 띄우고 health 에서 오류를 노출
            self._load_error = str(e)
            logger.exception("PaddleOCR 로딩 실패")

    def _get_ocr(self):
        if self._ocr is not None:
            return self._ocr
        with self._lock:
            if self._ocr is None:
                from paddleocr import PaddleOCR  # 무거운 import 는 실제 사용 시점에

                cfg = settings.ocr
                kwargs = dict(
                    use_doc_orientation_classify=cfg.use_doc_orientation,
                    use_doc_unwarping=cfg.use_doc_unwarping,
                    use_textline_orientation=cfg.use_textline_orientation,
                    text_det_limit_side_len=cfg.det_limit_side_len,
                    text_det_limit_type=cfg.det_limit_type,
                    device=cfg.device,
                    enable_mkldnn=cfg.enable_mkldnn,
                    cpu_threads=cfg.cpu_threads,
                )
                if cfg.det_model or cfg.rec_model:
                    if cfg.det_model:
                        kwargs["text_detection_model_name"] = cfg.det_model
                    if cfg.rec_model:
                        kwargs["text_recognition_model_name"] = cfg.rec_model
                else:
                    kwargs["lang"] = cfg.lang
                    kwargs["ocr_version"] = cfg.ocr_version

                logger.info(f"PaddleOCR 로딩: {kwargs}")
                self._ocr = PaddleOCR(**kwargs)
                self._load_error = None
        return self._ocr

    def recognize(self, image: np.ndarray) -> List[OcrLine]:
        """
        BGR 이미지 → 인식된 텍스트 박스 목록

        기울어진 사진에서 글자 영역은 찾았는데 글자를 못 읽은(빈 결과) 박스가 있으면,
        추정한 기울기만큼 사진을 바로 세워 한 번 더 인식하고 더 많이 읽은 쪽을 쓴다.
        (실측: 6° 기울기 + 흐림에서 '제주삼다수 500ml 950' 줄이 빈 글자로 인식됨. 반듯한 사진은 재시도 없음)
        """
        lines, failed = self._run(image)
        angle = math.degrees(math.atan(estimate_skew(lines)))
        if failed and abs(angle) >= 2.0:
            retry, failed2 = self._run(_rotate(image, angle))
            logger.info(f"기울어진 사진 재인식 angle={angle:.1f} 빈박스 {failed}→{failed2} 줄 {len(lines)}→{len(retry)}")
            if len(retry) > len(lines) or (len(retry) == len(lines) and failed2 < failed):
                return retry
        return lines

    def _run(self, image: np.ndarray) -> Tuple[List[OcrLine], int]:
        """한 번 인식 → (읽은 줄, 글자 영역은 찾았지만 읽지 못한 박스 수)"""
        ocr = self._get_ocr()
        with self._lock:
            results = ocr.predict(image)

        min_score = settings.ocr.min_score
        lines: List[OcrLine] = []
        failed = 0
        for res in results:
            texts = res["rec_texts"]
            scores = res["rec_scores"]
            boxes = res["rec_boxes"]
            # 기울어진 글자 영역(4점). 사진 기울기 추정에 사용 (없으면 수평으로 가정)
            polys = res.get("rec_polys") if hasattr(res, "get") else None
            if polys is None or len(polys) != len(texts):
                polys = [None] * len(texts)
            for text, score, box, poly in zip(texts, scores, boxes, polys):
                text = (text or "").strip()
                if not text or float(score) < min_score:
                    failed += 1
                    continue
                x1, y1, x2, y2 = (int(v) for v in box)
                pts = tuple((float(p[0]), float(p[1])) for p in poly) if poly is not None and len(poly) == 4 else None
                lines.append(OcrLine(text=text, score=round(float(score), 4), box=(x1, y1, x2, y2), poly=pts))
        return lines, failed


def _rotate(image: np.ndarray, angle: float) -> np.ndarray:
    """반시계 방향으로 angle 도 회전 (잘리지 않게 캔버스 확장, 여백은 흰색)"""
    import cv2

    h, w = image.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    cos, sin = abs(m[0, 0]), abs(m[0, 1])
    nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
    m[0, 2] += nw / 2 - w / 2
    m[1, 2] += nh / 2 - h / 2
    return cv2.warpAffine(image, m, (nw, nh), borderValue=(255, 255, 255))


ocr_engine = OcrEngine()
