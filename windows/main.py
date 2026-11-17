"""Reset Epson multi-modelo.

Uso:
  python main.py info            -> lista impressoras USB e Device ID
  python main.py ler             -> le contadores das almofadas (nao grava nada)
  python main.py reset           -> faz backup, zera contadores e confere (pede confirmacao)
  python main.py teste-jatos     -> imprime o padrao de teste de jatos
  python main.py limpeza         -> limpeza de cabecote
Opcoes: -v (mostra pacotes D4), --porta N (escolhe a impressora se houver mais de uma)
"""
import argparse
import datetime
import json
import os
import sys

from usb_win import list_usb_printers, UsbPrinter, parse_device_id
from d4 import D4Link, CTRL
from epson import Epson, achar_modelo, cmd_teste_jatos, cmd_limpeza, enviar_manutencao, salvar_backup


def impressoras_epson():
    res = []
    for p in list_usb_printers():
        if "vid_04b8" not in p.lower():
            continue
        try:
            with UsbPrinter(p) as u:
                did = parse_device_id(u.device_id())
        except OSError as e:
            did = {"erro": str(e)}
        res.append((p, did))
    return res


def escolher(args):
    lst = impressoras_epson()
    if not lst:
        sys.exit("Nenhuma impressora Epson USB encontrada.")
    if len(lst) > 1 and args.porta is None:
        for i, (p, d) in enumerate(lst):
            print(i, d.get("MDL", "?"), p)
        sys.exit("Mais de uma Epson conectada: use --porta N")
    return lst[args.porta or 0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("acao", choices=["info", "ler", "reset", "teste-jatos", "limpeza", "limpeza-intensa"])
    ap.add_argument("-v", action="store_true")
    ap.add_argument("--porta", type=int)
    ap.add_argument("--sim", action="store_true", help="nao pedir confirmacao")
    args = ap.parse_args()

    if args.acao == "info":
        for i, (p, d) in enumerate(impressoras_epson()):
            print("[%d] %s\n    MDL=%s  CMD=%s\n    %s" % (i, d.get("MDL"), d.get("MDL"), d.get("CMD", d.get("COMMAND SET")), p))
        return

    path, did = escolher(args)
    mdl = did.get("MDL", "")
    print("Impressora:", mdl)

    if args.acao in ("teste-jatos", "limpeza", "limpeza-intensa"):
        dados = {"teste-jatos": cmd_teste_jatos(), "limpeza": cmd_limpeza(False),
                 "limpeza-intensa": cmd_limpeza(True)}[args.acao]
        print("Comando enviado por", enviar_manutencao(mdl, path, dados))
        return

    chave, modelo = achar_modelo(mdl)
    if not modelo:
        sys.exit("Modelo %s nao esta em modelos.json" % mdl)
    print("Perfil:", chave)

    with UsbPrinter(path) as u, D4Link(u, verbose=args.v) as link:
        link.open(CTRL)
        ep = Epson(link, modelo)
        cont = ep.contadores()
        for nome, c in cont.items():
            pct = "%6.2f%%" % c["percentual"] if c["percentual"] is not None else "   n/d "
            print("  %-11s %s  (valor=%d bytes=%s)" % (nome, pct, c["valor"], c["bytes"]))

        if args.acao == "reset":
            if not ep.pode_resetar():
                sys.exit("Este perfil nao tem valores de reset definidos (so leitura).")
            nome = salvar_backup(chave, did, ep.backup())
            print("Backup salvo em", nome)
            if not args.sim and input("Gravar o reset dos contadores? (s/N) ").strip().lower() != "s":
                print("Cancelado.")
                return
            ep.resetar()
            for nome_c, c in ep.contadores().items():
                print("  depois: %-11s valor=%d" % (nome_c, c["valor"]))
            print("Reset concluido. Desligue e ligue a impressora.")


if __name__ == "__main__":
    main()
