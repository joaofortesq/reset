import tomllib, json, sys
db = tomllib.load(open(sys.argv[2] if len(sys.argv) > 2 else 'dl/epson.toml','rb'))['EPSON']
want = "L355 L210 L120 L365 L375 L380 L395 L495 L575 L606 L3150 L3110 L4150 L4160 L4260 L800 L5190 L5290".split()
# divisores (epson_print_conf, modelo igual ou irmao direto)
DIV = {
 "L355": (65.0, None, None), "L365": (62.07, None, None), "L375": (62.07, None, None), "L575": (62.07, None, None),
 "L380": (62.06, None, None), "L395": (62.06, None, None), "L495": (62.06, None, None),
 "L3150": (63.46, 34.16, None), "L3110": (63.46, 34.16, None), "L5190": (63.46, 34.16, None),
 "L4150": (109.13, 16.31, None), "L4160": (109.13, 16.31, None),
 "L5290": (63.46, 34.16, 13.0), "L4260": (63.46, 34.16, 13.0),
}
CONF = {  # A = mesma familia testada; B = 2 fontes; C = 1 fonte; D = atencao
 "L3150":"A","L3110":"A","L5190":"A","L4150":"A","L4160":"A","L375":"A","L575":"A","L5290":"A",
 "L355":"B","L210":"B","L365":"B","L380":"B","L395":"B","L495":"B",
 "L120":"C","L606":"C","L800":"D","L4260":"D"}
out = {"_fonte": "reinkpy (epson.toml) + epson_print_conf; L3150 validada em hardware em 13/11/2026"}
for e in db:
    hit = [w for w in want if w in e['models']]
    if not hit: continue
    rk = e['rkey']; rkey = [rk & 0xFF, rk >> 8]
    wk = e['wkey1']
    assert all(chr(ord(c)+1) == d for c, d in zip(wk, e['wkey'])), (hit, wk)
    reset, counters_addr = {}, []
    defined = True
    for m in e['mem']:
        if 'reset' in m:
            for a, v in zip(m['addr'], m['reset']): reset[a] = v
        elif m['desc'] == 'Waste counter':
            for a in m['addr']: reset[a] = 0
        else:
            defined = False   # grupo "(?)" sem valores de reset
    a = set(reset)
    cont = {}
    if {48,49,47} <= a:
        cont["principal"] = [48,49,47]; cont["sem_margem"] = [50,51,47]
        if 252 in a: cont["terceiro"] = [252,253,254]
    elif {48,49,54} <= a:   # L605/L606: 48,49 + 54
        cont["principal"] = [48,49,54]
    elif {24,25} <= a:
        cont["principal"] = [24,25,30]
    else:  # L800
        cont["principal"] = [28,29]; cont["sem_margem"] = [30,31]
    for w in hit:
        d = DIV.get(w, (None, None, None))
        nomes = list(cont)
        prof = {
          "aliases": sorted(set(e['models'])),
          "confianca": CONF[w],
          "read_key": rkey, "write_key": wk,
          "contadores": {n: {"enderecos": cont[n], "divisor": d[i] if i < 3 else None} for i, n in enumerate(nomes)},
          "reset": {str(k): v for k, v in sorted(reset.items())} if defined else None,
        }
        if w == "L5290": prof["commit"] = {"256": 254}
        out[w] = prof
missing = [w for w in want if w not in out]
print("faltando:", missing)
for w in want:
    p = out[w]; print(f"{w:6} {p['confianca']} rk={p['read_key']} wk={p['write_key']} reset={p['reset']} cont={ {k:(v['enderecos'],v['divisor']) for k,v in p['contadores'].items()} }")
json.dump(out, open(sys.argv[1], 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
