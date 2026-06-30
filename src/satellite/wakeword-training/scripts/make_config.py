import yaml, os
base = yaml.safe_load(open("/work/openWakeWord/examples/custom_model.yml"))
base.update({
    "model_name": "renfield",
    "target_phrase": ["Renfield"],          # informational (we inject our own DE/EN/IT clips)
    "custom_negative_phrases": [],
    "n_samples": 8000,
    "n_samples_val": 1500,
    "output_dir": "./my_custom_model",
    "rir_paths": ["./mit_rirs"],
    "background_paths": ["./background_clips"],
    "background_paths_duplication_rate": [1],
    "false_positive_validation_data_path": "./validation_set_features.npy",
    "feature_data_files": {"ACAV100M_sample": "./neg_features.npy"},
    "augmentation_rounds": 1,
    "batch_n_per_class": {"ACAV100M_sample": 1024, "adversarial_negative": 50, "positive": 50},
    "model_type": "dnn",
    "layer_size": 32,
    "steps": 50000,
    "max_negative_weight": 1500,
    "target_false_positives_per_hour": 0.2,
})
yaml.safe_dump(base, open("/work/renfield.yaml", "w"))
print("wrote /work/renfield.yaml"); print(yaml.safe_dump(base))
