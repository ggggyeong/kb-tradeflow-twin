from __future__ import annotations

import argparse
import json
import os


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--live-model",
        action="store_true",
        help="Run one real OpenAI native tool-calling agent request.",
    )
    args = parser.parse_args()
    from app.core.config import clear_settings_cache, get_settings

    clear_settings_cache()
    settings = get_settings()
    result: dict[str, object] = {
        "openai_key_configured": settings.openai_api_key is not None,
        "langsmith_key_configured": settings.langsmith_api_key is not None,
        "openai_live_agent": "SKIPPED",
        "langsmith_read": "SKIPPED",
    }
    if settings.langsmith_api_key is not None:
        from langsmith import Client

        client = Client(
            api_key=settings.langsmith_api_key.get_secret_value(),
            timeout_ms=15_000,
        )
        next(client.list_projects(limit=1), None)
        result["langsmith_read"] = "PASS"
    if args.live_model:
        if settings.openai_api_key is None:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        os.environ["TRADEFLOW_MODE"] = "live"
        os.environ["LANGSMITH_TRACING"] = "false"
        clear_settings_cache()
        from app.agents.common import validate_native_tool_chain
        from app.agents.critic import build_agent

        response = build_agent().invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            'TOOL_INPUT_JSON={"tool_name":"review_workflow_evidence",'
                            '"args":{"workflow_kind":"TRADE_CASE",'
                            '"expected_task_ids":["integration_smoke"],'
                            '"evidence_snapshot":{"integration_smoke":{"status":"READY"}}}}'
                        ),
                    }
                ]
            }
        )
        evidence = validate_native_tool_chain(response["messages"])
        result["openai_live_agent"] = (
            "PASS" if evidence and response["structured_response"].status == "SUCCESS" else "FAIL"
        )
        result["openai_model"] = get_settings().model_critic
        result["native_tool_calls"] = len(evidence)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
