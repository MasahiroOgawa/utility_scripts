import requests
import os
import sys
import argparse
import subprocess
import glob

def download_jpeg_segments(m3u8_file_path, base_url, output_directory):
    """
    Download JPEG video segments from M3U8 file
    
    Args:
        m3u8_file_path: Path to the M3U8 file
        base_url: Base URL where video segments are located
        output_directory: Directory to save downloaded files
        
    Returns:
        bool: True if download completed successfully, False otherwise
    """
    if not os.path.exists(m3u8_file_path):
        print(f"Error: M3U8 file '{m3u8_file_path}' not found.")
        return False
    
    os.makedirs(output_directory, exist_ok=True)

    with open(m3u8_file_path, 'r') as f:
        lines = f.readlines()

    jpeg_urls = []
    for line in lines:
        line = line.strip()
        if line.endswith(".jpeg"):
            if line.startswith("http://") or line.startswith("https://"):
                jpeg_urls.append(line)  # Full URL provided in M3U8
            else:
                jpeg_urls.append(base_url + line)  # Relative path, needs base_url

    print(f"Found {len(jpeg_urls)} JPEG video segments.")

    download_errors = 0
    skipped_files = 0
    for i, url in enumerate(jpeg_urls):
        filename = os.path.join(output_directory, f"video{i:04d}.jpeg")
        
        # Skip download if file already exists
        if os.path.exists(filename):
            print(f"File {filename} already exists, skipping download...")
            skipped_files += 1
            continue
            
        try:
            print(f"Downloading {url} to {filename}...")
            response = requests.get(url, stream=True)
            response.raise_for_status()

            with open(filename, 'wb') as out_file:
                for chunk in response.iter_content(chunk_size=8192):
                    out_file.write(chunk)
            print(f"Downloaded {filename}")
        except requests.exceptions.RequestException as e:
            print(f"Error downloading {url}: {e}")
            print("Skipping to next file.")
            download_errors += 1
        except Exception as e:
            print(f"An unexpected error occurred for {url}: {e}")
            print("Skipping to next file.")
            download_errors += 1

    if download_errors > 0:
        print(f"Download complete with {download_errors} errors.")
    else:
        print("Download complete successfully.")
    
    if skipped_files > 0:
        print(f"Skipped {skipped_files} files that already existed.")
    
    return True

def combine_jpegs_to_mp4(input_dir, output_file="output_video.mp4", framerate=30):
    """
    Combine JPEG files into MP4 video using ffmpeg
    
    Args:
        input_dir: Directory containing JPEG files
        output_file: Output MP4 filename
        framerate: Video framerate (default: 30)
        
    Returns:
        bool: True if combination successful, False otherwise
    """
    # Check if ffmpeg is available
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("Error: ffmpeg is not installed or not found in PATH")
        return False
    
    # Find all JPEG files in the directory
    jpeg_pattern = os.path.join(input_dir, "video*.jpeg")
    jpeg_files = sorted(glob.glob(jpeg_pattern))
    
    if not jpeg_files:
        print(f"No JPEG files found in {input_dir}")
        return False
    
    print(f"Found {len(jpeg_files)} JPEG files to combine")
    
    # Create input pattern for ffmpeg
    input_pattern = os.path.join(input_dir, "video%04d.jpeg")
    
    # FFmpeg command to combine JPEG sequence into MP4
    ffmpeg_cmd = [
        'ffmpeg',
        '-y',  # Overwrite output file if it exists
        '-framerate', str(framerate),
        '-i', input_pattern,
        '-c:v', 'libx264',
        '-pix_fmt', 'yuv420p',
        output_file
    ]
    
    try:
        print(f"Combining JPEG files into {output_file}...")
        subprocess.run(ffmpeg_cmd, check=True)
        print(f"Successfully created {output_file}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error running ffmpeg: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description='Download JPEG video segments from M3U8 file')
    parser.add_argument('m3u8_file_path', help='Path to the M3U8 file')
    parser.add_argument('base_url', help='Base URL where video segments are located (use empty string "" if M3U8 contains full URLs)')
    parser.add_argument('--output-dir', '-o', default='downloaded_jpeg_videos', help='Output directory for downloaded files (default: downloaded_jpeg_videos)')
    parser.add_argument('--combine-video', '-c', action='store_true', help='Combine downloaded JPEG files into MP4 video')
    parser.add_argument('--video-output', '-v', default='output_video.mp4', help='Output MP4 filename (default: output_video.mp4)')
    parser.add_argument('--framerate', '-f', type=int, default=30, help='Video framerate (default: 30)')
    
    args = parser.parse_args()
    
    m3u8_file_path = args.m3u8_file_path
    base_url = args.base_url
    output_directory = args.output_dir
    
    # Ensure base_url ends with / if it's not empty
    if base_url and not base_url.endswith('/'):
        base_url += '/'
    
    # Download JPEG segments
    download_success = download_jpeg_segments(m3u8_file_path, base_url, output_directory)
    
    if not download_success:
        sys.exit(1)
    
    # Combine JPEG files into MP4 if requested
    if args.combine_video:
        combine_success = combine_jpegs_to_mp4(output_directory, args.video_output, args.framerate)
        if not combine_success:
            print("Video combination failed, but downloaded files are available.")
            sys.exit(1)

if __name__ == "__main__":
    main()