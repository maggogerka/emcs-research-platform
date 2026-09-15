# Data policy

Participant recordings, names, consent information and other personal data must
not be committed to this repository. The desktop application and capture tool
write local recordings to data/recordings/, which is excluded by .gitignore.

Each CSV row contains a real ADS1115 sample, the processed envelope and detector
outputs, the latest MPU6050 sample, lead-off state, motion flag, algorithm
settings and experimental phase labels. Preserve the adjacent capture summary
when reporting sampling integrity.
