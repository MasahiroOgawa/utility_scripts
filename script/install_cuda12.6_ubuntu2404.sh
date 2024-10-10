#/bin/bash
# Explanation: This is a total install script of CUDA12.6.1, cudnn9.5.0 and related libraries to Ubuntu20.04 
# Author: Masahiro Ogawa
# Reference:
#   https://developer.nvidia.com/cuda-downloads
#   https://developer.nvidia.com/cudnn-downloads
###

# stop immediately after any error
set -e

# echo "[INFO] remove existing cuda to avoid compatible issues..."
# sudo apt remove --purge "nvidia-*" -y && sudo apt autoremove
# sudo apt remove --purge "cuda-*" -y && sudo apt autoremove
# sudo apt remove --purge "libcudnn*" -y && sudo apt autoremove
# sudo apt remove --purge "libnvidia-*" -y && sudo apt autoremove
# echo "[INFO] done removal."

# echo "[INFO] install CUDA Toolkit..."
# wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/cuda-ubuntu2404.pin
# sudo mv cuda-ubuntu2404.pin /etc/apt/preferences.d/cuda-repository-pin-600
# wget https://developer.download.nvidia.com/compute/cuda/12.6.1/local_installers/cuda-repo-ubuntu2404-12-6-local_12.6.1-560.35.03-1_amd64.deb
# sudo dpkg -i cuda-repo-ubuntu2404-12-6-local_12.6.1-560.35.03-1_amd64.deb
# sudo cp /var/cuda-repo-ubuntu2404-12-6-local/cuda-*-keyring.gpg /usr/share/keyrings/
# sudo apt-get update
# sudo apt-get -y install cuda-toolkit-12-6
# rm *.deb
# echo "[INFO] done CUDA Toolkit installation."

# echo "[INFO] install cuDNN..."
# wget https://developer.download.nvidia.com/compute/cudnn/9.5.0/local_installers/cudnn-local-repo-ubuntu2404-9.5.0_1.0-1_amd64.deb
# sudo dpkg -i cudnn-local-repo-ubuntu2404-9.5.0_1.0-1_amd64.deb
# sudo cp /var/cudnn-local-repo-ubuntu2404-9.5.0/cudnn-*-keyring.gpg /usr/share/keyrings/
# sudo apt-get update
# sudo apt-get -y install cudnn
# rm *.deb
# echo "[INFO] done."

echo "[INFO] verifying cudnn installation..."
sudo apt-get -y install libcudnn9-samples
# we need below libraries to compile cudnn samples.
sudo apt-get install libfreeimage3 libfreeimage-dev
cp -r /usr/src/cudnn_samples_v9/ $HOME
cd  $HOME/cudnn_samples_v9/mnistCUDNN
make clean && make
./mnistCUDNN
echo "[INFO] If you see 'Test passed!', installation successed."
