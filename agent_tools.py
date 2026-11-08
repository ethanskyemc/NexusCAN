from pathlib import Path

base_tools = [
    "send_message",
    "manage_task",
    "run_command",
    "view_file",
    "write_to_file",
    "replace_file_content",
    "multi_replace_file_content",
    "list_dir",
    "find_by_name",
    "grep_search",
    "search_web",
]

for p in Path(".agents/agents").glob("*.md"):
    txt = p.read_text(encoding="utf-8")
    if "\ntools:\n" in txt:
        continue
    tools = (["invoke_subagent", "schedule"] + base_tools) if p.stem == "team-lead" else base_tools
    block = "tools:\n" + "".join(f"  - {t}\n" for t in tools)
    txt = txt.replace("inheritMcp: true\n", block + "inheritMcp: true\n", 1)
    p.write_text(txt, encoding="utf-8")
    print(f"Updated {p}")
