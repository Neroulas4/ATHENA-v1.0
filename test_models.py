from athena.models import Prediction
def test_prediction():
    p = Prediction(prediction_id="x", subject="NVDA", thesis="test", horizon="1d", confidence=.7)
    assert p.confidence == .7
