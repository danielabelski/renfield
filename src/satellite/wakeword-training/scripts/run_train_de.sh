cd /work; export LD_LIBRARY_PATH=$(cat /work/ld.env):$LD_LIBRARY_PATH
echo "### DE_AUGMENT $(date +%T)"
python openWakeWord/openwakeword/train.py --training_config renfield_de.yaml --augment_clips 2>&1
echo "### DE_AUGMENT_DONE rc=$?"
python openWakeWord/openwakeword/train.py --training_config renfield_de.yaml --train_model 2>&1
echo "### DE_TRAIN_DONE rc=$?"; ls -la /work/my_custom_model/renfield_de.onnx 2>/dev/null
echo "### DE_ALL_DONE"
