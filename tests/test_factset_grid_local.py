from io import BytesIO
from PIL import Image, ImageDraw
from ats.data.sources.factset_chart_grid import locate_printed_grids, cell_words
from ats.data.sources.factset_report_layout import Word

def grid_image(scale=1, offset=0, broken=False):
    image=Image.new('RGB',(300*scale,200*scale),'white')
    draw=ImageDraw.Draw(image)
    for y in (100,125,150,175):
        draw.line((10*scale,(y-offset)*scale,290*scale,(y-offset)*scale),fill='black')
    for x in (10,80,150,220,290):
        if broken:
            continue
        draw.line((x*scale,(100-offset)*scale,x*scale,(175-offset)*scale),fill='black')
    out=BytesIO()
    image.save(out,format='PNG')
    return out.getvalue()

def test_grid_coordinates_are_measured_not_fixed():
    for scale,offset in [(1,0),(2,0),(1,40)]:
        grids=locate_printed_grids(grid_image(scale,offset))
        assert len(grids)==1
        assert (grids[0].row_count,grids[0].column_count)==(3,4)
        assert grids[0].region[1]==(100-offset)/200

def test_broken_grid_is_not_invented():
    assert locate_printed_grids(grid_image(broken=True))==()

def test_cross_border_tokens_are_not_assigned():
    words=(Word('-6.4%',(.2,.2,.4,.3),(1,1,1)),
           Word('999',(.45,.2,.6,.3),(1,1,2)))
    assert cell_words(words,(.1,.1,.5,.4))=='-6.4%'
