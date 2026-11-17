# Análise — AdjProg Epson L3150 (REA)

Pasta analisada: `Reset EPSON Collection\L3110 L3150 L4150 L4160\AdjProg Epson L3150\Epson L3150`

| Arquivo | Tamanho | O que é |
|---|---|---|
| `Adjprog.exe` | 13,8 MB | Programa principal. **Empacotado/criptografado** (entropia 7,9994/8; seções com nomes aleatórios `.ksshoa`, `.aitimu`; tabela de diretórios do PE adulterada). Análise estática inviável sem desempacotar. |
| `apdadrv.dll` | 100 KB | **Driver de comunicação** (classe `CApdadrv`, `CD4Controller`, `CPortController`). Não está protegido → totalmente analisado. |
| `StrGene.dll` | 86 KB | 1 export `GetStrImage(...)`: gera bitmap de texto (para imprimir padrões/etiquetas). Irrelevante para o reset. |
| `Keygen.exe` | 1,7 MB | Gerador de licença do crack. **Não analisado nem executado** (fora do escopo). |

SHA-256 `Adjprog.exe`: `9cd6d08f74007f731c5809bac99349bc80f691b9a0eee6e3cc2c6efbe35d33e9`
SHA-256 `apdadrv.dll`: `f2277c9f1f477a6bd06b4645bd818e241ce8352395b4d67bab87583aaebd36fd`

---

## 1. O que foi OBSERVADO no binário (`apdadrv.dll`, decompilado no Ghidra)

### 1.1 Abertura da porta USB
- Aceita nomes `LPT`, `USB`, `COM`; para USB faz `sscanf(nome, "EPUSB%d")`.
- Enumera interfaces com `SetupDiGetClassDevs` usando o GUID
  `{762BB1B4-5164-11D1-B00B-00C0D1570F9E}` (interface do driver Epson), lê o valor de registro
  `PortName` de cada dispositivo e compara com o nome pedido; abre o `DevicePath` com
  `CreateFileA(GENERIC_READ|GENERIC_WRITE)`. Fallback: `\\.\<nome>`.
- Também procura a string `vid_04b8` (Vendor ID da Epson).
- Envio/recepção: `WriteFile`/`ReadFile` (e `DeviceIoControl` para status/reset de porta).

### 1.2 Protocolo de transporte = IEEE 1284.4 ("D4")
Fluxo de `ADD4Negotiation` (0x10002030):
1. Lê o Device ID e exige `CMD:` contendo `D4`.
2. Envia a string de entrada no modo D4 (bytes literais no código):
   ```
   00 00 00 1B 01 40 45 4A 4C 20 31 32 38 34 2E 34 0A 40 45 4A 4C 0A 40 45 4A 4C 0A
   = "\0\0\0\x1b\x01@EJL 1284.4\n@EJL\n@EJL\n"
   ```
3. **Init** (canal de transação 0/0):  `00 00 00 08 01 00 | 00 10`  (cmd 0x00, revisão 0x10)
4. **OpenChannel** socket 2:  `00 00 00 11 01 00 | 01 02 02 <PtS hi lo> <StP hi lo> 00 00 00 00`

Cada pacote D4 tem cabeçalho de 6 bytes: `PSID SSID LEN_hi LEN_lo CREDIT CONTROL` (comprimento inclui o cabeçalho).

`ADD4SendAndReceiveCtrlCh` (0x10001e30) — **é por aqui que passam os comandos de serviço**:
1. `CreditRequest` (cmd 0x04) para o socket 2: `00 00 00 0D 01 00 | 04 02 02 FF FF FF FF`
2. Pacote de dados: `02 02 <len hi lo> 00 00` + payload
3. `Credit` (cmd 0x03) concede 1 crédito ao printer: `00 00 00 0B 01 00 | 03 02 02 00 01`
4. Lê respostas até achar cabeçalho `02 02 <len>`; devolve o payload (len − 6).

Socket **0x02 = canal de controle (EPSON-CTRL)**; socket **0x40 (`'@'`) = canal de dados** (usado em `ADD4SendDataToDataCh`).

> Conclusão: tudo que o AdjProg faz na impressora (ler/zerar contadores, limpeza, teste) é um
> *payload* enviado por `ADD4SendAndReceiveCtrlCh` ou `ADD4SendDataToDataCh`. Os payloads em si
> ficam dentro do `Adjprog.exe` criptografado.

---

## 2. O que é INFERIDO (protocolo Epson público — ainda não confirmado neste binário)

Estes formatos são amplamente documentados (gutenprint/escputil, reinkpy, epson_print_conf) e são
consistentes com o transporte acima, mas **os valores específicos do L3150 precisam ser capturados**
(ver seção 3).

### 2.1 Reset das almofadas = escrita na EEPROM (canal de controle)
- Leitura de 1 byte:
  `7C 7C 07 00 <RK0 RK1> 41 BE A0 <addr_lo> <addr_hi>`  → `"||"`, len 7, chave de leitura, `'A'`
- Escrita de 1 byte:
  `7C 7C 10 00 <RK0 RK1> 42 BD 21 <addr_lo> <addr_hi> <valor> <WK 8 bytes>`  → `'B'`, chave de escrita de 8 bytes
- O "reset" é escrever valores específicos (geralmente 0, às vezes um valor inicial) num conjunto de
  endereços do contador principal + contador de "platen pad"/borda, e às vezes um byte de flag.
- **Por modelo variam**: chave de leitura (2 bytes), chave de escrita (8 bytes), lista de
  endereços, valores de reset, e o valor máximo (para mostrar %).

### 2.2 Teste de jatos e limpeza = "remote mode" (canal de dados ou impressão crua)
```
ESC ( R 08 00 00 'REMOTE1'      # entra no remote mode
NC 02 00 00 00                  # Nozzle Check (imprime padrão)
CH 02 00 00 <grupo>             # Cleaning (00 = todas as cores; outros valores = grupo)
ESC 00 00 00                    # sai do remote mode
```
Precedido de `\0\0\0\x1b\x01@EJL 1284.4\n@EJL\n@EJL\n` (sair do modo pacote) quando se envia
como trabalho de impressão comum. Isso **não depende de chave** — funciona em praticamente toda
Epson jato de tinta, por isso a parte de limpeza/teste do seu programa é a mais fácil de generalizar.
(Limpeza "forte"/"power cleaning" e carga inicial de tinta do AdjProg costumam ser comandos de
serviço específicos — também a capturar.)

---

## 3. O que falta (desconhecido) e como obter

| Dado do L3150 | Onde está | Como obter |
|---|---|---|
| Chave de leitura/escrita EEPROM | `Adjprog.exe` (criptografado) | Captura em tempo real |
| Endereços e valores do reset de almofadas | idem | Captura em tempo real |
| Comando exato de limpeza/teste usado pelo AdjProg | idem | Captura em tempo real |

**Método recomendado (preciso e reaproveitável para os outros modelos):** com uma L3150 ligada via
USB, rodar o AdjProg e interceptar as funções exportadas de `apdadrv.dll`
(`ADD4SendAndReceiveCtrlCh`, `ADD4SendDataToDataCh`) com Frida, registrando os bytes de entrada e
resposta enquanto você executa *Waste ink pad counter → Check / Initialize*, *Head cleaning* e
*Nozzle check*. Como os outros AdjProgs da sua coleção provavelmente usam a mesma `apdadrv.dll`,
o mesmo script de captura serve para todos — e o resultado vira diretamente a tabela de modelos.

Alternativa sem printer: dump de memória do `Adjprog.exe` já desempacotado e busca pelos padrões
`7C 7C 10 00` / tabela de endereços — mais trabalhoso e menos confiável.

---

## 3b. Resultado (12/11/2026) — captura descartada, leitura validada no hardware

- A captura com Frida não registrou comandos: o AdjProg dá erro de comunicação neste PC
  (testou as portas USB001…USB335 e nunca negociou D4).
- Programa próprio (`epson_reset/`) lendo a L3150 real (USB `VID_04B8&PID_1143`):
  - Device ID: `MDL:L3150 Series; CMD:ESCPL2,BDC,D4,D4PX,ESCPR1,END4,GENEP`
  - Entrada D4: resposta `00 00 00 08 01 00 c5 00`; Init rev 0x10 aceito; canal 2 aberto.
  - Leitura EEPROM com chave `97 07` **aceita**: resposta `@BDC PS\r\nEE:0030F5;\x0c`.
  - Contador principal (end. 48,49,47) = 4085 → **64,37%**; sem margem (50,51,47) = 0%.
  - Saída de D4 (CloseChannel + Exit) confirmada.
- **Teste de jatos confirmado** (padrão impresso): job RAW pela fila online
  `EPSON L3150 Series (Copiar 1)` com `REMOTE1` + `NC 02 00 00 00`.
- **Reset confirmado (13/11/2026):** chave de escrita "Maribaya" aceita; os 10 endereços
  (48,49,47,52,53,54=94,50,51,55=94,28) foram gravados e conferidos por releitura.
  Contador principal 65,05% → 0,00%. Backup em `epson_reset/backup_L3150_20261113_080337.json`.
- **L3250 (13/11/2026, perfil L5290, USB `PID_118A`):** chave de leitura `4A 36` aceita (firmware
  não bloqueado); principal 32,90%, sem margem 0%, terceira 27,69%. Teste de jatos impresso.
  Reset ainda não executado (chave de escrita da família não confirmada).
- **Executável:** `epson_reset/dist/ManutencaoEpson.exe` (interface gráfica, 18 perfis + aliases).
- **Limpeza normal confirmada na L3250** (`TI` + `CH 00 00`, ciclo executado).
- **Reset confirmado na L3250:** chave de escrita "Maribaya" aceita; 14 endereços gravados e
  conferidos (principal 33,38% → 0, terceira 28,46% → 0). Byte 256 já era 0 (sem ajuste).
  Backup em `epson_reset/backups/backup_L5290_20261113_083937.json`.
- **Teste de jatos corrigido (13/11/2026):** a folha parava no meio (L3110/L3250). Capturado o
  trabalho do driver oficial (fila pausada, `spool\PRINTERS\*.SPL`): sequência
  `TI` + `JS` + `NC` / `VI` + `LD` / `FF` / `JE`. Mesmo com bytes, DEVMODE e datatype idênticos,
  só saiu completo quando o documento passou a se chamar **"Maintenance"** (o monitor de
  linguagem da Epson trata esse nome de forma especial). Confirmado na L3250 e na L3110
  (driver da L3110 envia os mesmos 132 bytes; folhas do driver e do programa saíram iguais).
- **Rede / Wi-Fi (16/11/2026):** SNMP v1 (comunidade "public"), comando EPSON-CTRL dentro do OID
  `1.3.6.1.4.1.1248.1.2.2.44.1.1.2.1.<bytes>`; descoberta por broadcast do OID de Device ID
  `1.3.6.1.4.1.1248.1.2.2.1.1.1.1.1`. L3250 em 192.168.0.115 encontrada em ~3 s; leitura
  (53,42% / 0% / 21,54%) e **reset de 14 endereços confirmados pela rede**. Backup em
  `epson_reset/backups/backup_L5290_rede_20261116_170616.json`.
- **Ainda não testado:** jatos/limpeza pela porta 9100; limpeza intensa (`CH 00 10`); sequência de jatos nova em modelos antigos
  (L375/L355).

## 4. Arquitetura sugerida para o programa multi-modelo

```
transporte/
  usb_windows.py   -> abre a impressora (usbprint/WinUSB ou libusb), Read/Write
  d4.py            -> IEEE 1284.4: enter, Init, OpenChannel(2 e 0x40), Credit, send/recv
comandos/
  eeprom.py        -> read(addr), write(addr, val) com chaves do modelo
  remote.py        -> nozzle_check(), cleaning(grupo)
modelos.json       -> por modelo: read_key, write_key, enderecos_reset{addr:valor},
                      enderecos_leitura_contador, max_contador
app                -> detecta modelo pelo Device ID (MDL:), mostra contador, reset, limpeza, teste
```

Fluxo do reset: detectar modelo pelo `MDL:` do Device ID → ler contadores (mostrar %) → confirmar
→ escrever os valores de reset → reler para verificar → pedir para desligar/ligar a impressora.

Cuidado: escrita em endereço errado da EEPROM pode bloquear a impressora; sempre faça *backup*
(ler e salvar os endereços antes de escrever) e valide cada modelo com uma captura real.
