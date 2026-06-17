import json
import sys
import asyncio
from agent import Agent, logo_to_ascii, MODE_META
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich import box
from rich.text import Text
from rich.rule import Rule
from rich.align import Align
from rich.status import Status

console = Console()
err_console = Console(stderr=True)

P = "bold purple"
PK = "bold bright_magenta"
W = "white"
D = "bright_black"


_autocorrect_on = False


def cls():
    print("\033c", end="")


def fmt_tokens(n: int) -> str:
    if n < 1000:
        return str(n)
    return f"{n/1000:.1f}k"


def header(agent: Agent):
    mc = agent.modes.get(agent.current_mode, {})
    meta = MODE_META.get(agent.current_mode, {})
    label = meta.get("label", agent.current_mode)
    model = mc.get("model", "?")
    t = Text.assemble(
        (" Mochi ", "white on bright_magenta"),
        (f" {label} ", "white on purple"),
        ("  " + model, D),
    )
    console.print(Panel(t, border_style="purple", padding=(0, 1)))


def help_panel():
    t = Table(box=box.SIMPLE_HEAD, show_header=False, border_style="purple", padding=(0, 2, 0, 0))
    t.add_column("", style=PK, no_wrap=True)
    t.add_column("", style=W)
    t.add_row("/work", "mode working - code, fichiers, commandes")
    t.add_row("/docs", "mode documentation - docs API, methodes")
    t.add_row("/debug", "mode debug - correction de bugs")
    t.add_row("", "")
    t.add_row("/save <nom>", "sauvegarder la session")
    t.add_row("/load <nom>", "charger une session")
    t.add_row("/maxtokens <n>", "regler max_tokens")
    t.add_row("/stats", "statistiques, tokens")
    t.add_row("/autodetect", "detection auto du mode on/off")
    t.add_row("/help", "afficher cette aide")
    t.add_row("/forget", "effacer la memoire")
    t.add_row("/clear", "nettoyer le terminal")
    t.add_row("/quit", "quitter")
    console.print(Panel(t, border_style="purple", padding=(1, 2)))


def show_logo():
    art = logo_to_ascii()
    if art:
        console.print(Panel(Align.center(art), border_style="purple", padding=(0, 2)))


async def main():
    try:
        agent = Agent()
    except Exception as e:
        err_console.print(f"[bold red]Erreur[/] {e}")
        sys.exit(1)

    cls()
    header(agent)
    help_panel()

    while True:
        try:
            user_input = Prompt.ask(f"\n[{PK}]>[/]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print(f"\n[{PK}]bye[/]")
            break

        if not user_input:
            continue

        if user_input == "/quit":
            console.print(f"[{PK}]bye[/]")
            break

        if user_input == "/help":
            help_panel()
            continue

        if user_input == "/clear":
            cls()
            header(agent)
            help_panel()
            continue

        if user_input == "/forget":
            agent.clear_memory()
            console.print(f"[{P}]memoire effacee[/]")
            continue

        if user_input == "/stats":
            s = agent.show_stats()
            t = s["tokens"]
            console.print(Panel(
                f"[{W}]mode   : [bold]{s['mode']}[/]\n"
                f"[{W}]modele : {s['model']}\n"
                f"[{W}]fallback: {s['fallback']}\n"
                f"[{W}]max    : {s['max_tokens']}\n"
                f"[{W}]messages: {s['memory']}\n"
                f"[{W}]tokens aujourd'hui:\n"
                f"  [{PK}]prompt   : {fmt_tokens(t['prompt']):>6}\n"
                f"  [{PK}]reponse  : {fmt_tokens(t['completion']):>6}\n"
                f"  [{PK}]total    : {fmt_tokens(t['total']):>6}[/]",
                border_style="purple",
                title="[bold white]stats[/]",
                title_align="left",
                padding=(1, 2),
            ))
            continue

        if user_input.startswith("/gen "):
            task = user_input[5:].strip()
            if not task:
                console.print("[bold red]utilisation: /gen <tache>[/]")
                continue
            console.print(Panel(Text("Planification de la tache...", style=P), border_style="purple"))
            result = await agent.gen(task)
            console.print()
            if result.startswith("[Erreur]"):
                console.print(f"[bold red]{result}[/]")
            else:
                console.print(Markdown(result))
            t = agent.token_usage
            console.print(f"[{D}]tokens: +{t['completion']} | total: {fmt_tokens(t['total'])}[/]")
            continue

        if user_input.startswith("/save "):
            name = user_input[6:].strip()
            if not name:
                console.print("[bold red]utilisation: /save <nom>[/]")
                continue
            console.print(f"[{P}]{agent.save_session(name)}[/]")
            continue

        if user_input.startswith("/load "):
            name = user_input[6:].strip()
            if not name:
                console.print("[bold red]utilisation: /load <nom>[/]")
                continue
            msg = agent.load_session(name)
            cls()
            console.print(f"[{P}]{msg}[/]")
            header(agent)
            help_panel()
            continue

        if user_input == "/autodetect":
            agent.auto_detect = not agent.auto_detect
            console.print(f"[{P}]detection auto: {'ON' if agent.auto_detect else 'OFF'}[/]")
            continue

        if user_input == "/autocorrect":
            global _autocorrect_on
            _autocorrect_on = not _autocorrect_on
            console.print(f"[{P}]autocorrect embarrassant: {'ON' if _autocorrect_on else 'OFF'}[/]")
            continue

        if user_input.startswith("/maxtokens "):
            try:
                val = int(user_input[11:].strip())
                console.print(f"[{P}]{agent.set_mode_max_tokens(val)}[/]")
            except ValueError:
                console.print("[bold red]utilisation: /maxtokens <nombre>[/]")
            continue

        if user_input in ("/work", "/docs", "/debug"):
            mode = user_input[1:]
            cls()
            console.print(f"[{P}]{agent.set_mode(mode)}[/]")
            show_logo()
            header(agent)
            help_panel()
            continue

        detected = agent.auto_detect_mode(user_input)
        if detected:
            agent.set_mode(detected)
            meta = MODE_META.get(detected, {})
            console.print(f"[{P}]Mode -> {meta.get('label', detected)}[/]")
        mc = agent.modes.get(agent.current_mode, {})
        model_name = mc.get("model", "?")
        content_text = ""
        tool_count = 0
        status = Status(f"[{D}]Agent Mochi en action...[/]", console=console)
        status.start()
        async for event, data in agent.generate_stream(user_input):
            if event == "plan":
                status.stop()
                lines = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(data))
                console.print(Panel(Text(lines, style="bold"), title="[bold purple]Plan[/]", border_style="purple", padding=(0, 1)))
                console.print(Rule(style="bright_black"))
                status.start()
            elif event == "step":
                status.stop()
                i, step_text = data
                console.print(f"  [{P}]Etape {i+1}: {step_text}[/]")
                status.start()
            elif event == "error":
                status.stop()
                console.print(f"[bold red]{data}[/]")
                break
            elif event == "content":
                if _autocorrect_on:
                    corrected, reaction = "".apply(data, 0.25)
                    if reaction:
                        content_text += corrected + "\n\n*" + reaction + "*"
                    else:
                        content_text += data
                else:
                    content_text += data
            elif event == "tool_call":
                status.stop()
                name, args = data
                tool_count += 1
                args_preview = {
                    k: (str(v)[:80] + ".." if len(str(v)) > 80 else v)
                    for k, v in args.items()
                }
                console.print()
                console.print(Panel(
                    Text(json.dumps(args_preview, ensure_ascii=False, indent=1)),
                    title=f"[bold bright_magenta]  {tool_count}. {name}[/]",
                    border_style="bright_magenta",
                    padding=(0, 1),
                ))
                status.start()
            elif event == "tool_result":
                status.stop()
                name, result = data
                console.print(f"  [{D}]-> {result[:200]}[/]")
                status.start()
        status.stop()
        if content_text:
            console.print()
            console.print(Markdown(content_text))
            console.print()
            t = agent.token_usage
            console.print(f"[{D}]tokens: +{t['completion']} | total: {fmt_tokens(t['total'])} | modele: {model_name}[/]")

    await agent.close()


if __name__ == "__main__":
    asyncio.run(main())
