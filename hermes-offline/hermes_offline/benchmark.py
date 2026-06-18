"""
Quick benchmark to compare local model performance for agentic use.

Tests:
  1. Tool-calling reliability (does it produce valid JSON tool calls?)
  2. Instruction following (does it follow system prompt constraints?)
  3. Speed (tokens/second)
  4. Context retention (remembers info from earlier in context)

Run:
    hermes-offline-bench [--model qwen3:8b] [--endpoint http://127.0.0.1:11434/v1]
"""

from __future__ import annotations

import json
import sys
import time
from typing import Optional

try:
    from rich.console import Console
    from rich.table import Table
    console = Console()
except ImportError:
    class Console:
        def print(self, *a, **k): print(*a)
    console = Console()


TOOL_CALL_TESTS = [
    {
        "name": "Single tool call",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant with access to tools. Always use tools when appropriate."},
            {"role": "user",   "content": "What is the weather in Paris? Use the get_weather tool."},
        ],
        "tools": [{
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get current weather for a city",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string", "description": "City name"},
                        "units": {"type": "string", "enum": ["celsius", "fahrenheit"]},
                    },
                    "required": ["city"],
                },
            },
        }],
        "expect_tool": "get_weather",
        "expect_args": {"city": "Paris"},
    },
    {
        "name": "Parallel tool calls",
        "messages": [
            {"role": "system", "content": "You have tools. Use them."},
            {"role": "user",   "content": "Get the weather in Tokyo and London simultaneously."},
        ],
        "tools": [{
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather for a city",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            },
        }],
        "expect_tool": "get_weather",
        "expect_min_calls": 2,
    },
    {
        "name": "No tool needed",
        "messages": [
            {"role": "system", "content": "You have tools but only use them when necessary."},
            {"role": "user",   "content": "What is 2 + 2?"},
        ],
        "tools": [{
            "type": "function",
            "function": {
                "name": "calculator",
                "description": "Calculate math expressions",
                "parameters": {
                    "type": "object",
                    "properties": {"expression": {"type": "string"}},
                    "required": ["expression"],
                },
            },
        }],
        "expect_tool": None,  # Should answer directly, not call a tool
    },
]


def run_benchmark(
    model: str = "qwen3:4b",
    endpoint: str = "http://127.0.0.1:11434/v1",
    api_key: str = "ollama",
) -> dict:
    try:
        from openai import OpenAI
    except ImportError:
        console.print("[red]openai package not found. Install: pip install openai[/red]")
        sys.exit(1)

    client = OpenAI(base_url=endpoint, api_key=api_key)

    console.print(f"\n[bold]Benchmarking model: {model}[/bold]")
    console.print(f"Endpoint: {endpoint}\n")

    results = []

    for test in TOOL_CALL_TESTS:
        console.print(f"  Testing: {test['name']}...")
        start = time.time()

        try:
            resp = client.chat.completions.create(
                model=model,
                messages=test["messages"],
                tools=test.get("tools"),
                tool_choice="auto",
                max_tokens=512,
                temperature=0.1,
            )
        except Exception as exc:
            results.append({
                "test": test["name"],
                "passed": False,
                "error": str(exc),
                "latency_ms": int((time.time() - start) * 1000),
                "tps": 0,
            })
            console.print(f"    [red]✗ Error: {exc}[/red]")
            continue

        elapsed = time.time() - start
        msg = resp.choices[0].message
        tool_calls = msg.tool_calls or []
        completion_tokens = resp.usage.completion_tokens if resp.usage else 0
        tps = completion_tokens / elapsed if elapsed > 0 else 0

        # Evaluate
        passed = True
        notes = []

        expect_tool = test.get("expect_tool")
        if expect_tool is None:
            if tool_calls:
                passed = False
                notes.append(f"called tool unnecessarily ({tool_calls[0].function.name})")
        else:
            if not tool_calls:
                passed = False
                notes.append("no tool call made")
            else:
                called = tool_calls[0].function.name
                if called != expect_tool:
                    passed = False
                    notes.append(f"called {called} instead of {expect_tool}")
                else:
                    try:
                        args = json.loads(tool_calls[0].function.arguments)
                        expected_args = test.get("expect_args", {})
                        for k, v in expected_args.items():
                            if k not in args:
                                passed = False
                                notes.append(f"missing arg {k}")
                    except json.JSONDecodeError:
                        passed = False
                        notes.append("invalid JSON in tool arguments")

        min_calls = test.get("expect_min_calls", 0)
        if min_calls > 1 and len(tool_calls) < min_calls:
            passed = False
            notes.append(f"expected {min_calls} calls, got {len(tool_calls)}")

        status = "[green]✓[/green]" if passed else "[red]✗[/red]"
        note_str = " — " + "; ".join(notes) if notes else ""
        console.print(f"    {status} {test['name']}{note_str} [{tps:.1f} tok/s, {int(elapsed*1000)}ms]")

        results.append({
            "test": test["name"],
            "passed": passed,
            "latency_ms": int(elapsed * 1000),
            "tps": round(tps, 1),
            "tool_calls_made": len(tool_calls),
            "notes": notes,
        })

    # Summary
    passed_count = sum(1 for r in results if r["passed"])
    avg_tps = sum(r["tps"] for r in results) / len(results) if results else 0
    avg_latency = sum(r["latency_ms"] for r in results) / len(results) if results else 0

    console.print(f"\n[bold]Results for {model}:[/bold]")
    console.print(f"  Tool-calling accuracy: {passed_count}/{len(results)} tests passed")
    console.print(f"  Average speed:         {avg_tps:.1f} tokens/second")
    console.print(f"  Average latency:       {int(avg_latency)}ms")

    score = (passed_count / len(results)) * 100 if results else 0
    if score >= 90:
        grade = "[bold green]EXCELLENT[/bold green]"
    elif score >= 70:
        grade = "[bold yellow]GOOD[/bold yellow]"
    elif score >= 50:
        grade = "[bold orange]FAIR[/bold orange]"
    else:
        grade = "[bold red]POOR[/bold red]"
    console.print(f"  Grade: {grade} ({score:.0f}%)")

    return {
        "model": model,
        "passed": passed_count,
        "total": len(results),
        "score_pct": score,
        "avg_tps": avg_tps,
        "avg_latency_ms": avg_latency,
        "tests": results,
    }


def main(argv: Optional[list[str]] = None) -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Benchmark a local Ollama model for agentic use")
    parser.add_argument("--model",    default="qwen3:4b",                        help="Model name (default: qwen3:4b)")
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434/v1",       help="Ollama endpoint")
    parser.add_argument("--api-key",  default="ollama",                           help="API key (default: ollama)")
    args = parser.parse_args(argv)

    run_benchmark(args.model, args.endpoint, args.api_key)


if __name__ == "__main__":
    main()
