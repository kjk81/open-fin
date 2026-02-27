from pathlib import Path


def test_worker_has_no_langgraph_or_langchain_imports():
    worker_source = Path(__file__).resolve().parents[1] / "worker.py"
    content = worker_source.read_text(encoding="utf-8").lower()

    assert "langgraph" not in content
    assert "langchain" not in content
    assert "from agent" not in content
    assert "import agent" not in content
