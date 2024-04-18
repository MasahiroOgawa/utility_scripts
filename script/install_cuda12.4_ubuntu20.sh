#/bin/bash
# Explanation: This is a total install script of CUDA12.3 and related libraries to Ubuntu20.04 
# Author: Masahiro Ogawa
# Reference:
#   whole procedure: https://qiita.com/cinchan/items/9718e1f26146dc5e3eaa
#   driver: https://developer.nvidia.com/cuda-downloads?target_os=Linux&target_arch=x86_64&Distribution=Ubuntu&target_version=20.04&target_type=deb_local
#   cudnn: https://developer.nvidia.com/rdp/cudnn-download
#   tips: https://qiita.com/Manyan3/items/628b3b22700fd569e8fb
###

echo "[INFO] remove existing cuda to avoid compatible issues..."
sudo apt remove --purge "nvidia-*" -y && sudo apt autoremove -y
sudo apt remove --purge "cuda-*" -y && sudo apt autoremove -y
sudo apt remove --purge "libcudnn*" -y && sudo apt autoremove -y
sudo apt remove --purge "libnvidia-*" -y && sudo apt autoremove -y
echo "[INFO] done removal."

echo "[INFO] install base..."
wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2004/x86_64/cuda-ubuntu2004.pin
sudo mv cuda-ubuntu2004.pin /etc/apt/preferences.d/cuda-repository-pin-600
wget https://developer.download.nvidia.com/compute/cuda/12.4.1/local_installers/cuda-repo-ubuntu2004-12-4-local_12.4.1-550.54.15-1_amd64.deb
sudo dpkg -i cuda-repo-ubuntu2004-12-4-local_12.4.1-550.54.15-1_amd64.deb
sudo cp /var/cuda-repo-ubuntu2004-12-4-local/cuda-*-keyring.gpg /usr/share/keyrings/
sudo apt-get update -y
sudo apt-get -y install cuda-toolkit-12-4
rm cuda-repo-ubuntu2004-12-4-local_12.4.1-550.54.15-1_amd64.deb
echo "[INFO] done base installation."

echo "[INFO] install driver..."
sudo apt-get install -y nvidia-driver-550-open
sudo apt-get install -y cuda-drivers-550
echo "[INFO] done installing the driver."

echo "[INFO] install cuDNN..."
wget https://developer.download.nvidia.com/compute/cudnn/9.1.0/local_installers/cudnn-local-repo-debian12-9.1.0_1.0-1_amd64.deb
sudo dpkg -i cudnn-local-repo-debian12-9.1.0_1.0-1_amd64.deb
sudo cp /var/cuda-repo-debian12-9-1-local/cudnn-*-keyring.gpg /usr/share/keyrings/
sudo add-apt-repository contrib
sudo apt-get update -y
sudo apt-get -y install cudnn
sudo apt-get -y install cudnn-cuda-12
rm cudnn-local-repo-debian12-9.1.0_1.0-1_amd64.deb
echo "[INFO] done. please reboot."

