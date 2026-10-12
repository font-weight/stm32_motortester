#!/usr/bin/env python3
"""Reproducible nominal circuit arithmetic, NOT hardware measurements or SPICE.
Python standard library only. The optional switched-capacitor model assumes ideal
switches, constant source/rail, no leakage, zero parasitics beyond named values.
"""
from math import pi, log, log10, exp, sqrt
from pathlib import Path
import json

OUT = Path(__file__).resolve().parents[1] / 'evidence' / 'calculations.json'
N=12; FADC=12e6; FS=50e3; VDDA=3.3; CADC=8e-12; RADC=1000.0
TS=71.5/FADC; LSB=VDDA/(2**N)

def db(x): return 20*log10(abs(x))
def parallel(a,b): return a*b/(a+b)
def ac_b(f, rs=10000., rb=100000., cc=1e-6, cf=2.2e-9, ro=1000., co=10e-9):
    s=2j*pi*f
    # Exact passive input network, ideal bias/signal followers and zero source R.
    h1=s*cc*rb/(1+s*(cc*(rs+rb)+rb*cf)+s*s*cc*rs*rb*cf)
    return h1/(1+s*ro*co)

def sample(ex, eh, r, cf, ts=TS):
    # Exact two-state linear RC response during track interval, relative to Vin.
    a=-(1/r+1/RADC)/cf; b=1/(RADC*cf)
    c=1/(RADC*CADC); d=-c
    disc=sqrt((a-d)**2+4*b*c)
    slow=(a+d+disc)/2; fast=(a+d-disc)/2
    u=exp(slow*ts); v=exp(fast*ts)
    # exp(A*t) = [u(A-fast*I)-v(A-slow*I)]/(slow-fast)
    den=slow-fast
    return (((u*(a-fast)-v*(a-slow))*ex+(u-v)*b*eh)/den,
            ((u-v)*c*ex+(u*(d-fast)-v*(d-slow))*eh)/den)

def settle(r, cf):
    # External cap already at full scale; held ADC cap starts at 0 V.
    ex=0.; eh=-VDDA; rows=[]
    for k in range(1,17):
        ex,eh=sample(ex,eh,r,cf)
        rows.append({'sample':k, 'hold_error_lsb':abs(eh)/LSB})
        ex*=exp(-(1/FS-TS)/(r*cf))
    return rows

rows=[]
for cyc in [1.5,7.5,13.5,28.5,41.5,55.5,71.5,239.5]:
    t=cyc/FADC
    rmax=t/(CADC*log(2**(N+2)))-RADC
    rows.append({'sample_cycles':cyc,'sample_us':t*1e6,
                 'conversion_us':(cyc+12.5)/FADC*1e6,
                 'formula_Rmax_ohm':rmax,'datasheet_Rmax_cap_ohm':min(rmax,50000),
                 'fits_20us_before_trigger_latency':(cyc+12.5)/FADC < 1/FS})
assert (71.5+12.5)/FADC < 1/FS
assert (239.5+12.5)/FADC > 1/FS
assert rows[6]['formula_Rmax_ohm'] > 50000

cs=1e-12; cground=200e-12; ctotal=cs+cground; plateau=.002
pickup=[]
for r in [1e5,1e6,1e7,1e8]:
    tau=r*ctotal
    pickup.append({'load_ohm':r,'gain':cs/ctotal,'tau_us':tau*1e6,
                   'highpass_hz':1/(2*pi*tau),'remaining_after_2ms':exp(-plateau/tau),
                   'droop_percent_after_2ms':100*(1-exp(-plateau/tau))})

out={
 'status':'calculated_nominal_NOT_MEASURED',
 'assumptions':{'fadc_hz':FADC,'fs_hz':FS,'vdda_v':VDDA,'adc_bits':N,
                'cadc_F':CADC,'radc_ohm':RADC,'sample_cycles':71.5,
                'quarter_lsb_settling_log_factor':log(2**(N+2))},
 'adc':{'ideal_lsb_v':LSB,'sample_settings':rows},
 'option_A':{'ladder_ohm':[1000]*4,'series_ohm':1000,'filter_F':10e-9,
             'rail_current_A':VDDA/4000,
             'nominal_taps_V':[.25*VDDA,.5*VDDA,.75*VDDA],
             'tap_thevenin_ohm':[parallel(1000,3000),parallel(2000,2000),parallel(3000,1000)],
             'worst_resistive_source_ohm':2000,
             'rc_corner_hz_at_midtap':1/(2*pi*2000*10e-9),
             'resistive_only_quarter_lsb_acq_us':(2000+RADC)*CADC*log(2**(N+2))*1e6,
             'single_pole_gain_at_25khz_db':db(1/(1+2j*pi*25000*2000*10e-9)),
             'initial_full_scale_charge_share_lsb':4096*CADC/(10e-9+CADC),
             'switched_capacitor_initial_condition':settle(2000,10e-9)},
 'option_B':{'rin_ohm':10000,'rbias_ohm':100000,'coupling_F':1e-6,'input_filter_F':2.2e-9,
             'output_series_ohm':1000,'output_filter_F':10e-9,
             'midband_gain_approx':100000/110000,
             'hp_hz_approx':1/(2*pi*110000*1e-6),
             'input_lp_hz_approx':1/(2*pi*parallel(10000,100000)*2.2e-9),
             'output_lp_hz':1/(2*pi*1000*10e-9),
             'gain_response':[{'frequency_hz':f,'magnitude':abs(ac_b(f)), 'gain_db':db(ac_b(f))}
                              for f in [1,10,100,1000,5000,10000,25000,50000,100000]],
             'droop_2ms_approx_percent':100*(1-exp(-.002/.11)),
             'bias_divider_tau_ms':parallel(10000,10000)*1e-6*1000,
             'coupling_settle_quarter_lsb_s':.11*log(2**14),
             'switched_capacitor_ideal_driver_initial_condition':settle(1000,10e-9),
             'illustrative_100nA_net_leakage_error_dc_mV':100e-9*100000*1000,
             'illustrative_100nA_net_leakage_error_midband_mV':100e-9*parallel(10000,100000)*1000},
 'pickup_ILLUSTRATIVE_NOT_COMPONENT_SELECTION':{
             'coupling_F':cs,'ground_capacitance_F':cground,'plateau_s':plateau,'cases':pickup,
             'load_for_5_percent_droop_ohm':-plateau/(log(.95)*ctotal),
             '100M_with_10M_probe_R_ohm':parallel(1e8,1e7),
             'gain_change_from_10pF_probe_percent':100*((ctotal/(ctotal+10e-12))-1),
             'gain_change_from_100pF_probe_percent':100*((ctotal/(ctotal+100e-12))-1)},
 'protection_ILLUSTRATIVE_NOT_FAULT_TEST':{
             'assumed_vf_v':.41,'clamp_upper_v_at_3V3':3.3+.41,'clamp_lower_v':-.41,
             'hypothetical_24V_current_through_10k_A':(24-3.3-.41)/10000,
             'hypothetical_24V_resistor_power_W':((24-3.3-.41)**2)/10000},
 'limitations':['No physical measurement, no transistor-level simulation, no validated sensor scaling.',
                'Rmax formula omits external capacitor and does not waive 50kΩ datasheet limit.',
                'Two-state model omits noise, leakage, op-amp dynamics, parasitics, tolerances, ADC kickback details.',
                'Clamp arithmetic does not establish protection, rail sink capability or a permitted 24V input.']
}
OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(OUT)
print(json.dumps({k:out[k] for k in ['adc','option_A','option_B']},indent=2))
