"""Offline OCR diagnostics; no production lookup table or source corrections."""
from pathlib import Path
from io import BytesIO
import json
from PIL import Image,ImageOps
from ats.data.sources.factset_report_layout import ocr_words,visual_lines

for name,col in [('sep-24-2',11),('sep-25-2',2),('sep-22-3',9),('aug-26-3',9)]:
    grid=json.loads(Path(f'tests/fixtures/factset_earnings_insight/grid-candidates/{name}.json').read_text())['grids'][0]
    cell=next(c for c in grid['cells'] if c['row']==1 and c['column']==col)
    with Image.open(f'/private/tmp/ats-factset-review/{name}.png') as image:
        box=tuple(round(v*d) for v,d in zip(cell['region'],(image.width,image.height,image.width,image.height)))
        original=image.crop(box).convert('L')
    results=[]
    for resample in [Image.Resampling.NEAREST,Image.Resampling.LANCZOS]:
        for threshold in [None,120,160,200,230]:
            im=ImageOps.expand(original,border=4,fill=255).resize((original.width*8+64,original.height*8+64),resample)
            if threshold is not None:
                im=im.point(lambda x:0 if x<threshold else 255)
            stream=BytesIO(); im.save(stream,format='PNG')
            for psm in [6,7]:
                words=ocr_words(stream.getvalue(),scale=1,psm=psm)
                token=' '.join(line for line,_ in visual_lines(words))
                results.append({'resample':int(resample),'threshold':threshold,'psm':psm,'token':token})
    print(json.dumps({'chart':name,'column':col,'attempts':results}),flush=True)
