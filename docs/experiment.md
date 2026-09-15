# Experimental protocol

The GUI implements one baseline phase followed by 30 labelled contractions:

1. Ten seconds of quiet baseline used for EMG calibration.
2. For each repetition: two seconds preparation, two seconds contraction and
   three seconds rest.
3. Weak, medium and strong labels repeat cyclically across the 30 trials.

The operator is responsible for verbal instructions and for verifying that
both lead-off inputs are low before collecting contraction data. A high LO− or
LO+ immediately places the firmware in LEADS_OFF and suppresses detector
events. Motion freezes baseline adaptation but remains visible in the saved
CSV.

Do not infer detector performance from an unlabelled capture. The analysis
script reports TP, FP, FN, precision, recall, F1, false positives per minute and
recognition latency only when experimental phase labels are present.

## Safety

This platform is a research prototype and is not a medical device. When
electrodes are connected to a person, the complete acquisition system,
including the computer interface, must be powered from an isolated autonomous
battery arrangement appropriate to the laboratory safety protocol. Do not use
a mains-connected experimental setup on a participant.
