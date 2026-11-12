"""업로드 이미지 디코딩/전처리"""
import io

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError


class InvalidImageError(ValueError):
    pass


def load_image(data: bytes, max_side: int) -> np.ndarray:
    """
    바이트 → BGR numpy 배열 (PaddleOCR 입력 형식).
    - 휴대폰 사진의 EXIF 회전 정보를 반영 (안 하면 옆으로 누운 이미지가 들어감)
    - 너무 큰 사진은 긴 변 기준으로 축소 (속도/메모리)
    """
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError) as e:
        raise InvalidImageError(f"이미지를 읽을 수 없습니다: {e}") from e

    image = ImageOps.exif_transpose(image)
    if image.mode != "RGB":
        # PNG 투명 배경(스크린샷 등)은 흰 배경으로 합성
        if image.mode in ("RGBA", "LA", "P"):
            image = image.convert("RGBA")
            background = Image.new("RGB", image.size, (255, 255, 255))
            background.paste(image, mask=image.split()[-1])
            image = background
        else:
            image = image.convert("RGB")

    longest = max(image.size)
    if max_side > 0 and longest > max_side:
        scale = max_side / longest
        image = image.resize(
            (round(image.width * scale), round(image.height * scale)),
            Image.LANCZOS,
        )

    rgb = np.asarray(image)
    return np.ascontiguousarray(rgb[:, :, ::-1])  # RGB → BGR
