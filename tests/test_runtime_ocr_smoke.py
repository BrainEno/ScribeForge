import json
from pathlib import Path

import pytest

from scribeforge.workers.runtime_ocr_smoke import check_sample


def test_sample_requires_recognizable_text_and_uses_a_temporary_image() -> None:
    images: list[Path] = []

    def worker() -> None:
        import sys

        image = Path(sys.argv[2])
        assert image.is_file()
        images.append(image)
        print(json.dumps({"lines": [{"text": "Scribe Forge OCR 2026"}]}))

    check_sample(worker)
    assert len(images) == 1 and not images[0].exists()
    with pytest.raises(ValueError, match="sample text"):
        check_sample(lambda: print('{"lines": [{"text": "garbage"}]}'))
