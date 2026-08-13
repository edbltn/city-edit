"""
Sheet layout for the two label stocks we print on.

Both are die-cut circles on US Letter carrier sheets, so the artwork does not
get cropped — it gets *registered*. The grid below has to match the die on the
physical sheet or every sticker prints off-centre, which is why each product
carries the source its numbers were confirmed against.

## The stocks

`STOCKS["2.5"]` — Amazon B0D1BRRPS7, "Methdic 2.5" Diameter High Visibility
Printable Round Labels, Matte White, 360 Customizable Blank Stickers". 12 labels
per 8.5×11 sheet, 30 sheets, 3 across × 4 down.

This grid is MEASURED FROM THE PRODUCT, not taken from a catalogue. Methdic
publishes Avery-template compatibility rather than a dimensioned drawing, so the
first version used the generic 12-up 2.5" layout — and it printed visibly off
the die. The generic layout is wrong on every axis by about 0.03":

    generic          real         error
    left  0.250      0.280        -0.030
    h gap 0.250      0.220        +0.030
    top   0.406      0.368        +0.038
    v gap 0.0625     0.088        -0.026

Those errors compound in OPPOSITE directions, which is what made it obvious on
paper rather than marginal: too much top margin plus too little row gap pushes
row 1 down and pulls row 4 up, and too little side margin plus too much column
gap splays the outer columns outward. Nothing lines up and nothing is uniformly
shifted, so it cannot be dialled out with a printer offset.

The real numbers come from measuring the manufacturer's own sheet photograph
(`data/sticker.jpg`) — the sheet's aspect matches Letter to 0.13%, and the
measured die comes out 2.4976" against a 2.5" nominal, so the scale is trustworthy
to about a thousandth. They close exactly:

    0.28  + 3(2.5) + 2(0.22)  + 0.28  = 8.500"
    0.368 + 4(2.5) + 3(0.088) + 0.368 = 11.000"

Still worth a --proof sheet against real stock before a long run: a photograph
is a good measurement, not a drawing.

`STOCKS["3"]` — Amazon B0D92RBZHN, "Premium Label Supply Glossy White Sticker
Round Labels – 3" Circle – (6 per Sheet)". 6 labels per 8.5×11 sheet, 60 labels
(10 sheets), glossy, LASER ONLY. 2 across × 3 down, also exact — but note this
one is still the CATALOGUE layout (OnlineLabels OL2279), not a measurement of
the product, so it carries the same risk the 2.5" turned out to have. Proof it
before a long run:

    0.75 + 3 + 1 + 3 + 0.75 = 8.500"
    0.5 + 3(3) + 2(0.5) + 0.5 = 11.000"

## Bleed

Bleed is not a taste decision here, it is bounded by the gap to the next label.
Ink that overshoots one die by more than half the gap lands on the neighbour.
The 12-up sheet's vertical gap is 0.088", so its bleed ceiling is 0.044"; the
6-up sheet has a 0.5" vertical gap and could take far more than it needs. Each
stock therefore carries its own bleed, and `validate()` re-derives the ceiling
from the grid rather than trusting the constant — which is why correcting the
12-up's gap did not silently invalidate its bleed.

## Glossy is laser only

The 3" stock has no inkjet coating. Sent through an inkjet the ink sits on the
surface, never dries, and smears on the first sticker you peel. The build prints
this warning on the proof sheet rather than hoping someone reads the listing.
"""


class Stock:
    def __init__(self, key, asin, title, die, across, down,
                 left_margin, top_margin, h_gap, v_gap, bleed,
                 printer, finish, sheets, source):
        self.key = key
        self.asin = asin
        self.title = title
        self.die = die
        self.across = across
        self.down = down
        self.left_margin = left_margin
        self.top_margin = top_margin
        self.h_gap = h_gap
        self.v_gap = v_gap
        self.bleed = bleed
        self.printer = printer
        self.finish = finish
        self.sheets = sheets
        self.source = source

    @property
    def per_sheet(self) -> int:
        return self.across * self.down

    @property
    def capacity(self) -> int:
        return self.per_sheet * self.sheets

    def centre(self, col: int, row: int) -> tuple[float, float]:
        """Centre of the label at (col, row), in inches from the sheet's
        top-left. Pitch is die + gap; the margin is to the label's EDGE."""
        x = self.left_margin + col * (self.die + self.h_gap) + self.die / 2
        y = self.top_margin + row * (self.die + self.v_gap) + self.die / 2
        return x, y


PAGE_W, PAGE_H = 8.5, 11.0

STOCKS = {
    "2.5": Stock(
        key="2.5", asin="B0D1BRRPS7",
        title='Methdic 2.5" round, matte white, 360 labels',
        die=2.5, across=3, down=4,
        left_margin=0.28, top_margin=0.368, h_gap=0.22, v_gap=0.088,
        bleed=0.030,
        printer="Inkjet or laser", finish="Matte white", sheets=30,
        source="amazon.com/dp/B0D1BRRPS7, grid measured from the "
               "manufacturer's sheet photo (data/sticker.jpg)",
    ),
    "3": Stock(
        key="3", asin="B0D92RBZHN",
        title='Premium Label Supply 3" round, glossy white, 60 labels',
        die=3.0, across=2, down=3,
        left_margin=0.75, top_margin=0.5, h_gap=1.0, v_gap=0.5,
        bleed=0.0625,
        printer="LASER ONLY — glossy stock has no inkjet coating",
        finish="Glossy white", sheets=10,
        source="amazon.com/dp/B0D92RBZHN + OnlineLabels OL2279 6-up 3\" grid",
    ),
}


def validate(stock: Stock, tol: float = 0.002) -> list[str]:
    """Check the grid actually closes on a Letter sheet, and that the bleed
    cannot reach the neighbouring label. Cheap, and it catches the one class of
    error that only shows up after you have printed thirty sheets."""
    problems = []

    w = (stock.left_margin * 2 + stock.across * stock.die
         + (stock.across - 1) * stock.h_gap)
    if abs(w - PAGE_W) > tol:
        problems.append(
            f'{stock.key}": columns span {w:.4f}", sheet is {PAGE_W}"'
        )

    h = (stock.top_margin * 2 + stock.down * stock.die
         + (stock.down - 1) * stock.v_gap)
    if abs(h - PAGE_H) > tol:
        problems.append(
            f'{stock.key}": rows span {h:.4f}", sheet is {PAGE_H}"'
        )

    ceiling = min(stock.h_gap, stock.v_gap) / 2
    if stock.bleed > ceiling:
        problems.append(
            f'{stock.key}": bleed {stock.bleed:.4f}" exceeds half the tightest '
            f'gap ({ceiling:.4f}") — ink would land on the next label'
        )
    if stock.bleed > stock.left_margin or stock.bleed > stock.top_margin:
        problems.append(
            f'{stock.key}": bleed {stock.bleed:.4f}" runs off the sheet edge'
        )

    return problems
