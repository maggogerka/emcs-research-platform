# Experimental protocol

The GUI implements a configurable calibration and prescribed-cue protocol:

1. It arms recording, marks `calibration_rest` and requests firmware EMG
   calibration. Trials remain blocked until the device emits the actual
   `EMG_CALIBRATION_DONE` event.
2. Each trial contains configurable prepare, contract-cue and rest durations
   (defaults 3, 2 and 3 seconds).
3. Weak, medium and strong prescribed instructions are balanced to within one
   trial and shuffled by a stored random seed (default 20260916).
4. Every transition is echoed by the firmware as a device-timestamped marker.
5. The session ends as `completed`, `aborted_by_user`, `lead_off` or
   `hardware_error`; it is never silently converted to a success.

The operator follows the visible Russian instruction/countdown card and verifies
that both lead-off inputs are low. A high LO- or LO+ immediately places the
firmware in LEADS_OFF, suppresses detector events and terminates the session as
`lead_off`. Motion freezes adaptive-baseline updates but remains recorded.
Pause emits its own marker and never removes already recorded samples.

Do not infer detector performance from an unlabelled capture. Analysis reports
TP, FP, FN, precision, recall, F1 and cue-to-detection latency only when hardware
phase markers are present. False positives per minute use only calibration,
prepare and rest exposure. Confidence intervals are percentile bootstraps over
trials. Prescribed intensity is an instruction, not measured force.

## Safety

This platform is a research prototype and is not a medical device. When
electrodes are connected to a person, the complete acquisition system must use
an appropriately isolated, autonomous battery-powered arrangement approved by
the laboratory protocol. Do not use a mains-connected experimental setup on a
participant.
