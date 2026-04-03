from __future__ import annotations

from dataclasses import dataclass

import cv2
import mss
import numpy as np
import pytesseract
from pytesseract.pytesseract import TesseractError, TesseractNotFoundError

from .config import Region


@dataclass(slots=True)
class OCRResult:
    text: str
    confidence: float


class OcrEngine:
    def __init__(self, tesseract_cmd: str | None = None) -> None:
        if tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        try:
            pytesseract.get_tesseract_version()
        except TesseractNotFoundError as error:
            raise RuntimeError(
                "Tesseract OCR was not found. Install Tesseract or set config.json:tesseract_cmd."
            ) from error
        self._sct = mss.mss()

    def read_region(self, region: Region, psm: int = 7, whitelist: str | None = None) -> OCRResult:
        monitor = {
            "left": region.left,
            "top": region.top,
            "width": region.width,
            "height": region.height,
        }
        shot = self._sct.grab(monitor)
        img = np.array(shot)
        bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        processed = _prepare_for_ocr(bgr)
        inverted = cv2.bitwise_not(processed)
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

        config = f"--oem 3 --psm {psm}"
        if whitelist:
            safe_whitelist = whitelist.replace('"', "").replace(" ", "")
            config += f" -c tessedit_char_whitelist={safe_whitelist}"

        best = _ocr_with_config(processed, config)
        if _good_enough(best):
            return best

        alt = _ocr_with_config(inverted, config)
        if _better(alt, best):
            best = alt
        if _good_enough(best):
            return best

        alt = _ocr_with_config(gray, config)
        if _better(alt, best):
            best = alt
        if _good_enough(best):
            return best

        alt = _ocr_with_config(gray, "--oem 3 --psm 6")
        if _better(alt, best):
            best = alt

        return best if best.text else OCRResult(text="", confidence=0.0)


def _ocr_with_config(image: np.ndarray, config: str) -> OCRResult:
    try:
        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT, config=config)
    except TesseractError:
        return OCRResult(text="", confidence=0.0)

    words: list[str] = []
    confidences: list[float] = []
    for i, raw_text in enumerate(data.get("text", [])):
        text = str(raw_text).strip()
        if not text:
            continue
        words.append(text)
        try:
            conf = float(data["conf"][i])
        except (ValueError, KeyError, IndexError):
            conf = -1.0
        if conf >= 0:
            confidences.append(conf)

    joined = " ".join(words).strip()
    confidence = float(sum(confidences) / len(confidences)) if confidences else 0.0
    return OCRResult(text=joined, confidence=confidence)


def _good_enough(result: OCRResult) -> bool:
    if not result.text:
        return False
    if result.confidence >= 24.0:
        return True
    return len(result.text) >= 8


def _better(candidate: OCRResult, current: OCRResult) -> bool:
    return (candidate.confidence, len(candidate.text)) > (current.confidence, len(current.text))


def _prepare_for_ocr(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    resized = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    denoised = cv2.bilateralFilter(resized, 9, 75, 75)
    _, thresholded = cv2.threshold(denoised, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return thresholded
