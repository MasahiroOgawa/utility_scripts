#/bin/bash
# Explanation: This is a total install script of CUDA12.3 and related libraries to Ubuntu20.04 
# Author: Masahiro Ogawa
# Reference:
#   https://qiita.com/cinchan/items/9718e1f26146dc5e3eaa
#   https://developer.nvidia.com/cuda-downloads?target_os=Linux&target_arch=x86_64&Distribution=Ubuntu&target_version=20.04&target_type=deb_local
###

echo "[INFO] remove existing cuda to avoid compatible issues..."
sudo apt remove --purge "nvidia-*" -y && sudo apt autoremove
sudo apt remove --purge "cuda-*" -y && sudo apt autoremove
sudo apt remove --purge "libcudnn*" -y && sudo apt autoremove
sudo apt remove --purge "libnvidia-*" -y && sudo apt autoremove
echo "[INFO] done removal."

echo "[INFO] install base..."
wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2004/x86_64/cuda-ubuntu2004.pin
sudo mv cuda-ubuntu2004.pin /etc/apt/preferences.d/cuda-repository-pin-600
wget https://developer.download.nvidia.com/compute/cuda/12.3.1/local_installers/cuda-repo-ubuntu2004-12-3-local_12.3.1-545.23.08-1_amd64.deb
sudo dpkg -i cuda-repo-ubuntu2004-12-3-local_12.3.1-545.23.08-1_amd64.deb
sudo cp /var/cuda-repo-ubuntu2004-12-3-local/cuda-*-keyring.gpg /usr/share/keyrings/
sudo apt-get update
sudo apt-get -y install cuda-toolkit-12-3
echo "[INFO] done base installation."

echo "[INFO] install driver..."
sudo apt-get install -y nvidia-kernel-open-545
sudo apt-get install -y cuda-drivers-545
echo "[INFO] done installing the driver."
