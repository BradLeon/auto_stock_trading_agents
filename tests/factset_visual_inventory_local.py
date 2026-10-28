"""Local-only extraction of unmodified PDF image evidence for visual review."""
from pathlib import Path
from pypdf import PdfReader

FILES = {
    "aug": "var/data_artifacts/ae/ae262138891244678730deaaef8607466b919f058038578fbea3b5454d95f381.bin",
    "sep": "var/data_artifacts/f6/f679661e1aaf0dc3eaa181e7fb44230a4c5c9c98ac6bea46eeea2af52edecc08.bin",
}
out = Path('/private/tmp/ats-factset-review')
out.mkdir(exist_ok=True)
for label, path in FILES.items():
    for number, page in enumerate(PdfReader(path).pages, 1):
        text = page.extract_text() or ''
        if any(title in text for title in ['Scorecard', 'Surprise', 'Growth', 'Net Profit Margin', 'Guidance', 'Geographic Revenue Exposure', 'Sector Level', 'Targets & Ratings']):
            for index, item in enumerate(page.images, 1):
                if item.image.width > 500:
                    item.image.save(out / f'{label}-{number}-{index}.png')
                    print(label, number, index, item.image.size)
