#!/usr/bin/env python3
"""Reaplica o patch de `litellm/types/utils.py::Message.__init__` que evita
o crash `pydantic_core.ValidationError: content ... Input should be a valid
string` quando um modelo de raciocinio (gpt-oss, qwen35, etc.) retorna
`content` como lista de blocos `[{"type":"reasoning",...}, {"type":"text",...}]`
em vez de string simples.

Mesma logica de `../references/BUGFIX-LITELLM-REASONING.md`, mas com uma
melhoria: prefere o bloco `"text"` final (a resposta de verdade) e só cai
para o texto de `"reasoning"` se nao existir nenhum bloco de texto -- a
versao documentada original concatenava reasoning+texto sempre, o que gruda
o paragrafo de raciocinio direto na frente do primeiro token de resposta
(sem o `record_delimiter` do graphrag entre eles) e corrompe o primeiro
registro extraido.

Por que este script existe (mesmo motivo do irmao em graphrag-operations):
o pacote fica em site-packages, fora de /workspaces -- um restart do
container apaga o patch. Rode de novo depois de qualquer restart.

Sequencia de patches: um unico patch, um unico arquivo
(`litellm/types/utils.py`, classe `Message.__init__`).

Idempotente: nao aplica duas vezes.

O comentario dentro do bloco `NEW` abaixo esta em ingles de proposito: e o
texto que efetivamente entra no arquivo do pacote (destino de um PR
upstream em github.com/BerriAI/litellm, um projeto em ingles).

ATENCAO (verificado 2026-09-22 no fork Escola-de-Matematica-Aplicada/graphrag,
que pina litellm==1.100.1): o texto-ancora (OLD) abaixo NAO bate mais com essa
versao -- `Message.__init__` foi reescrito (agora usa `Final[dict[str, Any]]`,
tem params dedicados `thinking_blocks`/`reasoning_content`/`reasoning_items`).
Rodar este script contra litellm>=~1.100 falha de forma segura (`[FALHOU]`,
exit 1, nao mexe no arquivo) -- nao assuma que "nao aplicou" significa "bug
corrigido": `Message(content=[{"type": "reasoning", ...}, {"type": "text",
...}])` chamado direto ainda lanca o mesmo `pydantic_core.ValidationError`
nessa versao. Antes de reusar este patch numa versao nova do litellm,
regenere OLD/NEW contra o `Message.__init__` atual (ou confirme que a camada
de transformacao de resposta do provedor especifico ja separa o bloco de
raciocinio antes de chegar em `Message(...)`, tornando o patch inteiro
desnecessario).
"""

import sys
from pathlib import Path

try:
    import litellm
except ImportError:
    print("ERRO: pacote litellm nao encontrado neste Python.", file=sys.stderr)
    sys.exit(1)

TARGET = Path(litellm.__file__).parent / "types" / "utils.py"

MARKER = "# Normalize list-style content (reasoning + text blocks) into a plain string."

OLD = """        init_values: Dict[str, Any] = {
            "content": content,
            "role": role or "assistant",  # handle null input"""

NEW = """        # Normalize list-style content (reasoning + text blocks) into a plain string.
        # Some reasoning models (e.g. gpt-oss, Qwen3 "thinking" variants) return
        # content as [{"type": "reasoning", "summary": [...]}, {"type": "text", ...}]
        # instead of a plain string, which fails validation below (`content` is
        # typed Optional[str]). Prefer the final "text" block (the actual answer);
        # only fall back to the reasoning summary text if no text block exists at
        # all, so callers still get *something* instead of a hard crash.
        if isinstance(content, list):
            _text_parts: List[str] = []
            _reasoning_parts: List[str] = []
            for _item in content:
                if isinstance(_item, dict):
                    if _item.get("type") == "text" and "text" in _item:
                        _text_parts.append(_item["text"])
                    elif _item.get("type") == "reasoning" and "summary" in _item:
                        for _s in _item.get("summary", []):
                            if isinstance(_s, dict) and "text" in _s:
                                _reasoning_parts.append(_s["text"])
                elif isinstance(_item, str):
                    _text_parts.append(_item)
            content = "\\n".join(_text_parts) if _text_parts else ("\\n".join(_reasoning_parts) or None)

        init_values: Dict[str, Any] = {
            "content": content,
            "role": role or "assistant",  # handle null input"""


def main() -> None:
    text = TARGET.read_text(encoding="utf-8")
    if MARKER in text:
        print(f"[skip] litellm Message.__init__: ja aplicado ({TARGET})")
        return
    if OLD not in text:
        print(
            f"[FALHOU] litellm Message.__init__: trecho esperado nao encontrado em {TARGET}"
        )
        print(
            "  A versao do litellm instalada pode ter mudado a estrutura interna do arquivo."
        )
        sys.exit(1)
    text = text.replace(OLD, NEW, 1)
    TARGET.write_text(text, encoding="utf-8")
    print(f"[ok] litellm Message.__init__: aplicado em {TARGET}")
    print("Limpando __pycache__ do litellm para forcar recompilacao...")
    import shutil

    for p in TARGET.parent.parent.rglob("__pycache__"):
        shutil.rmtree(p, ignore_errors=True)
    print(
        "Pronto. Valide com uma chamada real via litellm.acompletion a um modelo de raciocinio."
    )


if __name__ == "__main__":
    main()
