import pathlib

t = pathlib.Path("scripts/work503_baseline.py").read_text(encoding="utf-8")
pat = ('            x = batch[:, :, :N_FEATURES].float()\n'
       '            y = batch[:, -1, N_FEATURES + N_PRIOR + TARGET].float()\n'
       '            pred = core(x).reshape(-1)\n'
       '            loss = nn.functional.mse_loss(pred, y)')
print("count train pattern:", t.count(pat))
pat2 = ('            x = batch[:, :, :N_FEATURES].float()\n'
        '            y = batch[:, -1, N_FEATURES + N_PRIOR + TARGET].float()\n'
        '            pred = core(x).reshape(-1)\n'
        '            vs += float(nn.functional.mse_loss(pred, y))')
print("count valid pattern (12sp):", t.count(pat2))
pat3 = ('                x = batch[:, :, :N_FEATURES].float()\n'
        '                y = batch[:, -1, N_FEATURES + N_PRIOR + TARGET].float()\n'
        '                pred = core(x).reshape(-1)\n'
        '                vs += float(nn.functional.mse_loss(pred, y))')
print("count valid pattern (16sp):", t.count(pat3))
i = t.find("vs += float")
print(repr(t[i - 220:i + 60]))
