import pytest
from helpers import make_ctx

from app.schemas.tour import TourInput
from app.settings import Settings, save_settings
from app.tools.tour import handle

NEXT_STEPS = {"cs": "## Co dál", "en": "## Next steps"}


@pytest.mark.parametrize("lang", ["cs", "en"])
def test_tour_shows_overview_and_tour_and_ends_with_next_steps(workspace, lang):
    save_settings(workspace, Settings(language=lang))
    ctx, buf = make_ctx(workspace)
    out = handle(ctx, TourInput())
    assert out.markdown.startswith(ctx.texts.prewritten("overview").strip()[:20])
    last_heading = [line for line in out.markdown.splitlines() if line.startswith("## ")][-1]
    assert last_heading == NEXT_STEPS[lang]
    out.render(ctx)
    assert "Sorting Cap" in buf.getvalue()


def test_tour_always_available(workspace):
    save_settings(workspace, Settings(tour_shown=True))
    ctx, _ = make_ctx(workspace)
    assert handle(ctx, TourInput()).markdown
