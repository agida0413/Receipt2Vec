"""
PaddleOCR(3.x, PP-OCRv5) 래퍼

- 모델은 프로세스당 1회만 로딩 (서버 시작 시 warmup)
- PaddleOCR 파이프라인은 스레드 안전하지 않으므로 Lock 으로 직렬화
"""
import logging
import threading
from typing import List, Optional

import numpy as np

from app.core.config import settings
from app.services.ocr.layout import OcrLine

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
        """BGR 이미지 → 인식된 텍스트 박스 목록"""
        ocr = self._get_ocr()
        with self._lock:
            results = ocr.predict(image)

        min_score = settings.ocr.min_score
        lines: List[OcrLine] = []
        for res in results:
            texts = res["rec_texts"]
            scores = res["rec_scores"]
            boxes = res["rec_boxes"]
            for text, score, box in zip(texts, scores, boxes):
                text = (text or "").strip()
                if not text or float(score) < min_score:
                    continue
                x1, y1, x2, y2 = (int(v) for v in box)
                lines.append(OcrLine(text=text, score=round(float(score), 4), box=(x1, y1, x2, y2)))
        return lines


ocr_engine = OcrEngine()
