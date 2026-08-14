"""MODULE 71 — Validation Statistics (Aug 2026, fix-everything campaign).

The math that decides whether ANY backtest number can be believed. Pure
stdlib implementations of the Bailey & Lopez de Prado statistics, verified
against every published anchor before adoption (test file pins them):

  psr      Probabilistic Sharpe Ratio (Bailey & Lopez de Prado, J. Risk 2012)
  emax_z   E[max] of N standard normals (EVT, Bailey-LdP 2014 App.1)
  dsr      Deflated Sharpe Ratio = PSR at the expected-max-of-N-trials bar
           (Bailey & Lopez de Prado, JPM 40(5), 2014)
  mintrl   Minimum Track Record Length (J. Risk 2012)
  minbtl   Minimum Backtest Length in years for N trials ("Pseudo-Mathematics
           and Financial Charlatanism", Notices of the AMS, 2014, Thm 2)
  neff     effective trial count under average pairwise correlation

THE OPERATING FACT THIS MODULE ENCODES (ledger 2026-08-14): at 6 months of
daily bars with the campaign's 32 disclosed trials, noise alone is expected
to produce a best in-sample annualized Sharpe of ~2.97, and DSR>0.95 demands
~3.0-4.9 — so NOTHING can honestly pass at this data length. Gate-0 of the
validation pipeline (docs/EXECUTION_AUDIT + ledger) calls minbtl() BEFORE any
backtest: 32 trials require ~4.4 years. The multi-year fetch is the
precondition for any verdict, not an enhancement.
"""
import math

# ---- stdlib normal cdf / inverse (Acklam) ----
def ncdf(x): return 0.5*math.erfc(-x/math.sqrt(2.0))
def nppf(p):
    a=[-3.969683028665376e+01,2.209460984245205e+02,-2.759285104469687e+02,1.383577518672690e+02,-3.066479806614716e+01,2.506628277459239e+00]
    b=[-5.447609879822406e+01,1.615858368580409e+02,-1.556989798598866e+02,6.680131188771972e+01,-1.328068155288572e+01]
    c=[-7.784894002430293e-03,-3.223964580411365e-01,-2.400758277161838e+00,-2.549732539343734e+00,4.374664141464968e+00,2.938163982698783e+00]
    d=[7.784695709041462e-03,3.224671290700398e-01,2.445134137142996e+00,3.754408661907416e+00]
    pl=0.02425
    if p<pl:
        q=math.sqrt(-2*math.log(p))
        x=(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5])/((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    elif p<=1-pl:
        q=p-0.5; r=q*q
        x=(((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q/(((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
    else:
        q=math.sqrt(-2*math.log(1-p))
        x=-(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5])/((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    # one Halley refinement
    e=ncdf(x)-p; u=e*math.sqrt(2*math.pi)*math.exp(x*x/2)
    return x-u/(1+x*u/2)

EMC=0.5772156649015329

def emax_z(N):
    """Expected max of N iid standard normals (Bailey-LdP approx)."""
    return (1-EMC)*nppf(1-1.0/N)+EMC*nppf(1-1.0/(N*math.e))

def psr(sr, T, skew, kurt, sr_star=0.0):
    """Probabilistic Sharpe Ratio. sr, sr_star in SAME periodicity. kurt = raw (3=normal)."""
    denom=math.sqrt(1-skew*sr+((kurt-1)/4.0)*sr*sr)
    return ncdf((sr-sr_star)*math.sqrt(T-1)/denom)

def dsr(sr, T, skew, kurt, N, var_sr_trials):
    """sr per-period; var_sr_trials = Var of trial SRs in SAME per-period units."""
    sr0=math.sqrt(var_sr_trials)*emax_z(N)
    return psr(sr,T,skew,kurt,sr0), sr0

def mintrl(sr, skew, kurt, p=0.95, ref=0.0):
    """Bailey-LdP MinTRL, in observations. sr per-period."""
    return 1+(1-skew*sr+((kurt-1)/4.0)*sr*sr)*(nppf(p)/(sr-ref))**2

def minbtl(N, emax_target=1.0):
    """Minimum Backtest Length in YEARS (annualized SR target)."""
    return (emax_z(N)/emax_target)**2


# ---------------------------------------------------------------------------
# Verified against Bailey & Lopez de Prado (2014) JPM 40(5) worked example:
#   ann SR=2.5, N=100, Var[{SR}]=0.5 ann, T=1250, skew=-3, kurt=10
#   -> emax_z(100)=2.5306 ; SR0_daily=0.113172 ; DSR=0.900397  (paper: 0.9004)
#   -> N=46 gives DSR=0.9505 (paper: 0.9505)
#   -> normal returns, N=88 gives DSR=0.9505 (paper: 0.9505)
# Verified against Bailey/Borwein/LdP/Zhu (2014) Notices AMS:
#   -> minbtl(45, 1.0) = 4.998 years (paper: 5)
# ---------------------------------------------------------------------------

def neff(M, rho_bar):
    """Effective independent trials from M correlated trials (DSR paper App.3)."""
    return rho_bar + (1.0 - rho_bar) * M

if __name__ == "__main__":
    import math
    assert abs(emax_z(100) - 2.530603) < 1e-5
    assert abs(dsr(2.5/math.sqrt(250), 1250, -3.0, 10.0, 100, 0.5/250)[0] - 0.900397) < 1e-5
    assert abs(minbtl(45, 1.0) - 5.0) < 0.01
    print("all published anchors reproduced")
