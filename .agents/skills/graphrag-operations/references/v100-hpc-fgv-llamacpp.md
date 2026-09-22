# V100 real (HPC FGV on-premises) + llama.cpp local para GraphRAG drift (2026-08-28)

**Cluster real, não confundir com o sandbox A100 de `vllm-local-hpc-fgv.md`.** Head node
`hpcbo1fpr0001.acad.fgv.br` (só submissão, sem GPU). Nó GPU `hpcbo1fpr0009`, **Tesla
V100-PCIE-32GB**, driver `580.65.06` (CUDA 13.0 no `nvidia-smi`). Filas PBS reais:
`pesquisador` (CPU) e **`gpu`** (não `workq`!). Diretiva certa: `#PBS -q gpu` +
`#PBS -l select=1:ncpus=N:mem=Xgb` — **nunca** `ngpus=` no `select` (o manual do cluster,
`docs/hpc/Manual HPC On Premises FGV.md`, diz que `select=1:ngpus=1` não é suportado
nesse cluster). Walltime máx da fila `gpu`: 99h.

## O bug de fundo: CUDA 13 abandonou a V100 (Volta, sm_70)

- `pip install torch`/`vllm` traz por padrão build CUDA 13 (`torch==2.13.0+cu130` era o
  default em 28/08/2026). Esse build **não tem kernels para compute capability 7.0**:
  `torch.cuda` lança `no kernel image is available for execution on the device` só de
  fazer um matmul. O próprio erro do PyTorch já sugere o fix: reinstalar
  `torch==<mesma versão> --index-url https://download.pytorch.org/whl/cu126`.
- **O nvcc da toolchain CUDA 13 também não compila mais para sm_70**:
  `nvidia/cu13/bin/nvcc --list-gpu-arch` começa em `compute_75` (Turing+). Não adianta
  só trocar o torch se for compilar algo do zero (ex.: llama.cpp) — precisa de um nvcc
  de verdade da série 12.x.
- Ao reinstalar só `torch` como build cu126, **`torchvision`/`torchaudio` também precisam
  ser reinstalados na mesma build cu126** (senão import quebra: "PyTorch and torchvision
  were compiled with different CUDA major versions").

## Fix para vLLM (embeddings bge-m3 funcionando na V100)

1. `uv pip install torch==<versão> torchvision==<versão> torchaudio==<versão>
   --index-url https://download.pytorch.org/whl/cu126 --reinstall` (pegar as versões
   exatas que o `vllm` já resolveu antes, só trocando o índice/build).
2. **Mesmo com torch cu126, o próprio vLLM tem uma extensão nativa pré-compilada
   (`vllm._C_stable_libtorch`) linkada contra `libcudart.so.13`** — trocar o torch não
   recompila essa extensão. Sintoma: `ImportError: libcudart.so.13: cannot open shared
   object file`. **Fix**: manter o `libcudart.so.13` acessível via
   `LD_LIBRARY_PATH=.venv-vllm/lib/python3.12/site-packages/nvidia/cu13/lib` (sobra da
   instalação original do torch cu13, antes de reinstalar) — ele só precisa existir para
   o import, o cálculo pesado roda pelos kernels cu126 do torch normalmente.
3. Rodar com `--dtype half` (V100 não suporta bf16: cap < 8.0) e
   `--gpu-memory-utilization` baixo (~0.2 é suficiente pro bge-m3, sobra VRAM pro
   modelo de completion). Confirmado: `embed OK dim=1024`, vLLM detecta sozinho
   `TRITON_ATTN` como backend de atenção (FlashAttention-2 exige cap>=8, cai pra
   Triton automaticamente, sem precisar configurar nada).

## vLLM 0.28.0 NÃO tem suporte a GGUF (regressão/remoção real, não bug de config)

Confirmado por inspeção exaustiva do pacote instalado: **zero** ocorrência de
`gguf`/`GGUF` em `vllm/model_executor/model_loader/` (não existe `gguf_loader.py`), e o
docstring de `LoadConfig.load_format` em `vllm/config/load.py` lista todos os formatos
suportados (`auto, pt, safetensors, instanttensor, npcache, dummy, tensorizer,
runai_streamer, runai_streamer_sharded, sharded_state, mistral, modelexpress`) — `gguf`
não está lá. **Não tentar `vllm serve arquivo.gguf` nesta versão (ou provavelmente
qualquer vLLM recente).** Para servir `.gguf`, usar llama.cpp.

## Por que compilar llama.cpp do zero (e não usar binário pronto)

- Releases do `ggml-org/llama.cpp` (`github.com/ggml-org/llama.cpp/releases/tags/bNNNNN`)
  **só publicam binário Linux+CUDA para nada** — os CUDA prebuilts só existem para
  Windows (`llama-bNNNNN-bin-win-cuda-*.zip`). Linux só tem CPU, Vulkan, SYCL, ROCm,
  OpenVINO.
- Vulkan prebuilt não serve neste nó: sem `libvulkan.so.1`/ICD instalado
  (`llama-cli --list-devices` retorna `(none)`, driver aqui parece ser um instalação
  "compute-only" sem componentes Vulkan).
- Logo: compilar do zero com `GGML_CUDA=ON`, `CMAKE_CUDA_ARCHITECTURES=70`, usando uma
  toolchain CUDA 12.6 (única forma de gerar código pra sm_70 hoje).

## Como montar um CUDA 12.6 toolkit funcional sem root e sem `pip install cuda-toolkit`

Pacotes pip `nvidia-cuda-nvcc-cu12` (qualquer versão testada, até `12.4.131`) **não
trazem mais o binário `nvcc`/`cicc`, só `ptxas`**. E o pacote pip unificado
`nvidia-cuda-nvcc` (sem sufixo `-cuXX`) só existe pra CUDA 13.x. Solução que funcionou:
baixar componentes individuais do canal conda `nvidia` (não precisa de `conda`/`mamba`
instalado, só `curl`+`unzip`+`zstd`+`tar`, todos presentes no sistema):

```bash
# repodata pra descobrir nomes de arquivo exatos
curl -s https://conda.anaconda.org/nvidia/linux-64/repodata.json -o repodata.json

# pacotes com o BINÁRIO real do compilador (não confundir com o metapacote "cuda-nvcc"
# que só tem ~17KB de metadata e depende de "cuda-nvcc_linux-64")
curl -sL -o cuda-nvcc-tools.conda   https://conda.anaconda.org/nvidia/linux-64/cuda-nvcc-tools-12.6.85-0.conda
curl -sL -o cuda-crt-tools.conda    https://conda.anaconda.org/nvidia/linux-64/cuda-crt-tools-12.6.85-0.conda
curl -sL -o cuda-nvvm-tools.conda   https://conda.anaconda.org/nvidia/linux-64/cuda-nvvm-tools-12.6.85-0.conda
curl -sL -o cuda-nvvm-impl.conda    https://conda.anaconda.org/nvidia/linux-64/cuda-nvvm-impl-12.6.85-0.conda

# .conda = zip contendo pkg-*.tar.zst (payload) + info-*.tar.zst (metadata)
unzip pkg.conda && unzstd pkg-*.tar.zst && tar -xf pkg-*.tar
```

**Armadilha cara**: os pacotes `cuda-cudart-static`/`cuda-cudart-dev` do canal conda
`nvidia` (qualquer versão) empacotam `lib/libcudadevrt.a` etc como **symlinks quebrados**
apontando pra `../targets/x86_64-linux/lib/...`, e esse diretório `targets/` não vem
incluído no pacote `linux-64` genérico — `cp`/`ls` "acham" o arquivo mas
`cat`/`ld`/`cp -L` falham com "No such file or directory" porque o alvo do link não
existe. **Não perder tempo tentando outras versões desses pacotes conda** — a fonte
certa é o **redistributable oficial da NVIDIA** (mesmo lugar de onde o `pip` puxa os
wheels `nvidia-cuda-*`), que tem os `.a`/`.so` de verdade:

```bash
curl -s https://developer.download.nvidia.com/compute/cuda/redist/redistrib_12.6.3.json -o redistrib.json
# ver d['cuda_cudart']['linux-x86_64']['relative_path'] e d['cuda_cccl'][...]
curl -sL -o cuda_cudart.tar.xz https://developer.download.nvidia.com/compute/cuda/redist/cuda_cudart/linux-x86_64/cuda_cudart-linux-x86_64-12.6.77-archive.tar.xz
curl -sL -o cuda_cccl.tar.xz   https://developer.download.nvidia.com/compute/cuda/redist/cuda_cccl/linux-x86_64/cuda_cccl-linux-x86_64-12.6.77-archive.tar.xz
tar -xf cuda_cudart.tar.xz   # lib/{libcudart.so.12,libcudart_static.a,libcudadevrt.a,libculibos.a,stubs/libcuda.so} — arquivos REAIS
tar -xf cuda_cccl.tar.xz     # include/{nv,cuda,cub,thrust} — dá o header nv/target que falta (cuda_fp16.h inclui <nv/target>)
```

### Layout final que o `nvcc`/CMake conseguem resolver (paths relativos importam)

`nvcc` procura `cicc` em `<bin>/../nvvm/bin/cicc` e os stubs `crt/link.stub` em
`<bin>/crt/`. CMake's `FindCUDAToolkit` procura libs em `<bin>/../lib` (não `lib64` —
por isso o symlink `lib -> lib64`).

```
root/
├── bin/{nvcc,ptxas,cudafe++,fatbinary,nvlink,bin2c}      # de cuda-nvcc-tools (conda)
│   └── crt/{link.stub,prelink.stub}                      # de cuda-crt-tools (conda)
├── nvvm/{bin/cicc,libdevice/*.bc,include,lib64}           # de cuda-nvvm-tools+impl (conda)
├── include/                                                # MERGE de 3 fontes:
│   ├── crt/*.h (host_config.h etc)      <- pip nvidia-cuda-nvcc-cu12 (tem os headers,
│   │                                        só não tem o binário nvcc)
│   ├── cuda_runtime.h, cuda_fp16.h etc  <- pip nvidia-cuda-runtime-cu12 (ou nvidia/cuda_runtime)
│   ├── nv/, cuda/, cub/, thrust/        <- redistrib oficial cuda_cccl (não tem no pip nem conda genérico)
│   └── cublas*.h                        <- pip nvidia-cublas-cu12
├── lib -> lib64                                            # symlink (CMake espera bin/../lib)
├── lib64/                                                   # BUILD-time (linker precisa dos nomes
│   ├── libcudart.so.12, libcudart.so (symlink)                  versionados exatos p/ resolver -lcudart etc)
│   ├── libcudadevrt.a, libcudart_static.a, libculibos.a     <- redistrib oficial cuda_cudart (reais, não symlink quebrado)
│   ├── libcublas.so(.12), libcublasLt.so(.12)               <- symlink pro pip nvidia-cublas-cu12 já instalado no .venv-vllm
│   ├── libcuda.so.1 -> stubs/libcuda.so                     <- stub de LINK-TIME só (NUNCA no LD_LIBRARY_PATH em runtime!)
│   └── stubs/libcuda.so                                     <- redistrib oficial cuda_cudart, lib/stubs/
└── lib64-runtime/                                            # RUNTIME only: cópia de libcudart*/libcublas*
                                                                 SEM libcuda.so.1 (ver armadilha abaixo)
```

```bash
export PATH="$ROOT/bin:$PATH"
cmake -B build -S . -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=70 \
  -DCMAKE_CUDA_COMPILER=$ROOT/bin/nvcc -DCUDAToolkit_ROOT=$ROOT \
  -DCMAKE_BUILD_TYPE=Release -DLLAMA_BUILD_SERVER=ON
# durante a config/build, LD_LIBRARY_PATH precisa do $ROOT/lib64 (com o stub libcuda.so.1)
# senão o link final do llama-server falha com "libcublas.so.12 ... not found"
LD_LIBRARY_PATH="$ROOT/lib64" cmake --build build -j 16 --target llama-server llama-cli
```

### Armadilha mais cara de todas: stub de `libcuda.so` mascarando o driver real em runtime

Depois de compilar, rodar `llama-server` com `LD_LIBRARY_PATH` incluindo o mesmo
`$ROOT/lib64` do build (que tem o `libcuda.so.1` -> stub) faz o servidor **cair pra CPU
silenciosamente**: loga só um aviso (`ggml_cuda_init: failed to initialize CUDA: CUDA
driver is a stub library` / `warning: no usable GPU found`) e continua rodando — não é
erro fatal, é fácil não perceber (sintoma real percebido pelo usuário: "a GPU não está
sendo usada", confirmado depois via `nvidia-smi`; e no log, tempo de "pronto" de
~140-225s e geração a ~5 tok/s ao invés de 15s/~40-126 tok/s). **Fix**: ter um
`lib64-runtime/` separado (cudart+cublas, sem `libcuda.so.1`) e usar SÓ ele no
`LD_LIBRARY_PATH` do script que roda o servidor. O driver real
(`/usr/lib/x86_64-linux-gnu/libcuda.so.1` neste nó) é achado pelo loader via path
padrão do sistema, sem precisar declarar nada.

## Resultados confirmados nesta sessão (V100, GPU real)

- `unsloth/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-GGUF`, quant `UD-Q4_K_M` (25.27GB no
  repo): arquitetura GGUF `nemotron_h_moe` (híbrido Mamba-2 + MoE + poucas camadas de
  atenção; 3B params ativos de 30B totais). **~126 tok/s** de geração.
- `unsloth/Muse-Glimmer-30B-GGUF`, quant `UD-Q4_K_XL` (15.88GB): **~38 tok/s**.
- `--speculative-config '{"method": "mtp", "num_speculative_tokens": 3}'` é sintaxe
  válida no vLLM 0.28.0 (`vllm/config/speculative.py`), mas não foi testado de fato
  contra o `nemotron_h_moe` via llama.cpp nesta sessão (llama-server usado sem MTP).

## GraphRAG CLI 3.1.1: `query` é posicional, não `--query`

```bash
graphrag query --root <dir> --method drift "pergunta aqui"   # certo
graphrag query --root <dir> --method drift --query "..."     # ERRADO: "No such option: --query"
```

## `settings.yaml` final (`graphrag-full-setores`, 2026-08-28)

- `default_completion_model`: `agnes-2.5-flash` via `https://apihub.agnes-ai.com/v1`
  (API cloud, chave em `${AGNES_API_KEY}` no `.env`) — usado por padrão em todos os
  métodos de query, **não precisa de GPU**.
- `local_completion_model`: aponta pro llama.cpp local (`127.0.0.1:8010`, modelo
  Nemotron) — alternativa 100% local, não usada por padrão; trocar
  `drift_search.completion_model_id`/etc para `local_completion_model` se quiser rodar
  sem depender da API cloud.
- `default_embedding_model`: bge-m3 via vLLM local (`127.0.0.1:8001`) — **sempre
  local**, roda na fila `gpu`. `drift`/`local`/`basic` search todos precisam de
  embedding, então mesmo usando `agnes` pra completion, ainda é preciso subir o vLLM
  bge-m3 dentro do job antes de rodar a query.

## Scripts validados (`hpc/`)

- `run_gpu_diag.sh` / `submit_gpu_diag.sub` — diagnóstico rápido (torch.cuda + import
  vLLM + embed de teste). Reescrever o corpo pra cada nova coisa que precisa checar.
- `run_llamacpp_compare.sh` / `submit_llamacpp_compare.sub` — sobe cada modelo GGUF via
  `llama-server`, mede tempo de resposta, derruba, sobe o próximo.
- `run_drift_query.sh` / `submit_drift.sub` — sobe só o vLLM bge-m3 (embedding) e roda
  `graphrag query --method drift` contra a API `agnes` (completion). Este é o job de
  produção pra responder perguntas de drift.

Todos usam `#PBS -q gpu` + `select=1:ncpus=N:mem=Xgb` (sem `ngpus=`), rodam em
`hpcbo1fpr0009` de verdade (confirmar sempre com `hostname` no início do log).
