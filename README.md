# Manutenção Epson

Ferramenta interna de assistência técnica para impressoras Epson tanque de tinta:
**reset das almofadas**, **verificação de jatos**, **limpeza de cabeçote** e **limpeza intensa**,
pela **USB** ou pela **rede Wi‑Fi**.

| Pasta | Conteúdo |
|---|---|
| `windows/` | Programa para Windows (Python). Gera o `ManutencaoEpson.exe`. |
| `android/` | Aplicativo Android (Kotlin). O APK é gerado pelo GitHub Actions. |
| `docs_analise.md` | Análise do AdjProg e registro dos testes em impressoras reais. |

## Como funciona

- **USB:** protocolo IEEE 1284.4 (D4), o mesmo do AdjProg, canal EPSON‑CTRL.
- **Rede:** SNMP v1 (comunidade `public`), mesmos comandos dentro do OID
  `1.3.6.1.4.1.1248.1.2.2.44.1.1.2.1.<bytes>`; descoberta por broadcast.
- **EEPROM:** leitura `|| 07 00 <chave> 41 BE A0 <end>`; gravação `|| 10 00 <chave> 42 BD 21 <end> <valor> <chave de escrita +1>`.
  Toda gravação é conferida por releitura e precedida de backup.
- **Jatos e limpeza:** mesma sequência do driver oficial (capturada da fila do Windows).

A tabela `windows/modelos.json` (18 perfis + modelos da mesma família) é a mesma nos dois programas.
Ela foi gerada por `windows/ferramentas_gerar_modelos.py` a partir de bases públicas
(reinkpy — AGPL‑3.0; epson_print_conf) — **verificar as licenças antes de qualquer uso comercial**.

## Testado em aparelho real

| Função | USB | Rede |
|---|---|---|
| Ler / resetar | L3150, L3250, L375 | L3250 |
| Verificar jatos | L3250, L3110 | — |
| Limpeza de cabeçote | L3250 | — |

## Gerar o programa do Windows

```powershell
cd windows
.\gerar_exe.ps1
```

## Instalar o app Android

1. Abra a aba **Actions** do repositório → execução mais recente de "Gerar APK Android" → baixe **ManutencaoEpson-apk**.
2. No celular, abra o `.apk` e permita instalar de fontes desconhecidas.
3. USB: cabo **USB‑C → USB‑B** (ou adaptador OTG). Rede: celular e impressora na mesma rede Wi‑Fi.
