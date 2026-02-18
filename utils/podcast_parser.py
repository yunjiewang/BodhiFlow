"""
Podcast RSS parsing and audio downloading utilities for BodhiFlow.

This module provides functions to:
- Parse podcast RSS feeds to extract episode information
- Download podcast audio files
- Extract podcast metadata
"""

import re
import os
import time
import logging
from pathlib import Path
from typing import List, Dict, Optional
from urllib.parse import urlparse, urljoin
from datetime import datetime

import requests
import feedparser
from .logger_config import get_logger
from .input_handler import clean_filename, is_audio_url

logger = get_logger(__name__)


def get_podcast_info(rss_url: str) -> Dict:
    """
    Extract podcast-level metadata from RSS feed.
    
    Args:
        rss_url: Podcast RSS feed URL
        
    Returns:
        Dict containing podcast metadata: {
            "title": str, 
            "description": str, 
            "total_episodes": int, 
            "language": str, 
            "author": str
        }
    """
    try:
        logger.info(f"Fetching podcast info from: {rss_url}")
        
        # Parse the RSS feed
        feed = feedparser.parse(rss_url)
        
        if feed.bozo:
            logger.warning(f"RSS feed has parsing issues: {feed.bozo_exception}")
        
        # Extract basic info with fallbacks
        title = feed.feed.get('title', 'Unknown Podcast')
        description = feed.feed.get('description', '') or feed.feed.get('subtitle', '')
        
        # Clean up description (remove HTML tags if present)
        description = re.sub(r'<[^>]+>', '', description).strip()
        
        # Get language
        language = feed.feed.get('language', 'en')
        
        # Get author/creator
        author = (feed.feed.get('author', '') or 
                 feed.feed.get('itunes_author', '') or 
                 feed.feed.get('managingEditor', '') or
                 'Unknown')
        
        # Count total episodes
        total_episodes = len(feed.entries)
        
        podcast_info = {
            "title": title,
            "description": description[:500] + "..." if len(description) > 500 else description,
            "total_episodes": total_episodes,
            "language": language,
            "author": author
        }
        
        logger.info(f"Successfully extracted podcast info: {title} ({total_episodes} episodes)")
        return podcast_info
        
    except Exception as e:
        logger.error(f"Failed to get podcast info from {rss_url}: {str(e)}")
        return {
            "title": "Unknown Podcast",
            "description": "",
            "total_episodes": 0,
            "language": "en",
            "author": "Unknown"
        }


def parse_podcast_rss(rss_url: str, start_index: int = 1, end_index: int = 0) -> List[Dict]:
    """
    Parse podcast RSS feed to extract episode information.
    
    Args:
        rss_url: Podcast RSS feed URL
        start_index: First episode to process (1-based index)
        end_index: Last episode to process (0 for all episodes)
        
    Returns:
        List of episode info dictionaries: [{
            "title": str,
            "audio_url": str,
            "description": str,
            "pub_date": str,
            "duration": str
        }, ...]
    """
    try:
        logger.info(f"Parsing podcast RSS: {rss_url}")
        
        # Parse the RSS feed
        feed = feedparser.parse(rss_url)
        
        if feed.bozo:
            logger.warning(f"RSS feed has parsing issues: {feed.bozo_exception}")
        
        if not feed.entries:
            logger.error("No episodes found in RSS feed")
            return []
        
        episodes = []
        
        # RSS feeds typically have newest episodes first, but we want to respect user's index preferences
        total_episodes = len(feed.entries)
        logger.info(f"Found {total_episodes} episodes in feed")
        
        # Apply index filtering
        if end_index == 0:
            end_index = total_episodes
        
        # Ensure indices are within bounds
        start_index = max(1, start_index)
        end_index = min(total_episodes, end_index)
        
        if start_index > end_index:
            logger.warning(f"Start index ({start_index}) is greater than end index ({end_index})")
            return []
        
        # Extract episodes within the specified range
        for i in range(start_index - 1, end_index):  # Convert to 0-based indexing
            entry = feed.entries[i]
            
            try:
                # Extract episode title
                title = entry.get('title', f'Episode {i + 1}')
                
                # Find audio URL - look for enclosures or links
                audio_url = None
                
                # Method 1: Look for enclosures (most common)
                if hasattr(entry, 'enclosures') and entry.enclosures:
                    for enclosure in entry.enclosures:
                        if hasattr(enclosure, 'type') and 'audio' in enclosure.type.lower():
                            audio_url = enclosure.href
                            break
                        elif hasattr(enclosure, 'href') and is_audio_url(enclosure.href):
                            audio_url = enclosure.href
                            break
                
                # Method 2: Look in links
                if not audio_url and hasattr(entry, 'links'):
                    for link in entry.links:
                        if (hasattr(link, 'type') and 'audio' in link.get('type', '').lower()) or \
                           is_audio_url(link.get('href', '')):
                            audio_url = link.href
                            break
                
                # Method 3: Look for media content (some feeds use this)
                if not audio_url and hasattr(entry, 'media_content'):
                    for media in entry.media_content:
                        if 'audio' in media.get('type', '').lower():
                            audio_url = media.get('url')
                            break
                
                if not audio_url:
                    logger.warning(f"No audio URL found for episode: {title}")
                    continue
                
                # Extract description
                description = (entry.get('description', '') or 
                             entry.get('summary', '') or 
                             entry.get('subtitle', ''))
                
                # Clean up description (remove HTML tags)
                description = re.sub(r'<[^>]+>', '', description).strip()
                
                # Extract publication date
                pub_date = ""
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    try:
                        pub_date = datetime(*entry.published_parsed[:6]).strftime("%Y-%m-%d")
                    except:
                        pass
                
                if not pub_date and hasattr(entry, 'published'):
                    pub_date = entry.published
                
                # Extract duration
                duration = ""
                if hasattr(entry, 'itunes_duration'):
                    duration = entry.itunes_duration
                elif hasattr(entry, 'duration'):
                    duration = entry.duration
                
                episode_info = {
                    "title": title,
                    "audio_url": audio_url,
                    "description": description[:300] + "..." if len(description) > 300 else description,
                    "pub_date": pub_date,
                    "duration": duration
                }
                
                episodes.append(episode_info)
                logger.debug(f"Extracted episode: {title}")
                
            except Exception as e:
                logger.error(f"Failed to parse episode {i + 1}: {str(e)}")
                continue
        
        logger.info(f"Successfully parsed {len(episodes)} episodes from RSS feed")
        return episodes
        
    except Exception as e:
        logger.error(f"Failed to parse podcast RSS {rss_url}: {str(e)}")
        return []


def download_podcast_audio(audio_url: str, output_path: str, episode_title: str) -> Optional[str]:
    """
    Download podcast audio file from URL.
    
    Args:
        audio_url: Direct URL to podcast audio file
        output_path: Directory to save audio file
        episode_title: Episode title for filename
        
    Returns:
        Path to downloaded audio file or None on failure
    """
    try:
        logger.info(f"Downloading podcast audio: {episode_title}")
        
        # Ensure output directory exists
        os.makedirs(output_path, exist_ok=True)
        
        # Clean filename
        safe_title = clean_filename(episode_title)
        
        # Get file extension from URL
        parsed_url = urlparse(audio_url)
        url_path = parsed_url.path
        file_ext = os.path.splitext(url_path)[1].lower()
        
        # Default to .mp3 if no extension found
        if not file_ext or file_ext not in ['.mp3', '.m4a', '.wav', '.aac', '.ogg']:
            file_ext = '.mp3'
        
        # Create output filename
        output_filename = f"{safe_title}{file_ext}"
        output_file_path = os.path.join(output_path, output_filename)
        
        # Check if file already exists
        if os.path.exists(output_file_path):
            logger.info(f"Audio file already exists: {output_file_path}")
            return output_file_path
        
        # Download with retries
        max_retries = 3
        for attempt in range(max_retries):
            try:
                logger.info(f"Download attempt {attempt + 1} for: {audio_url}")
                
                # Set up headers to mimic a browser
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                    'Accept': 'audio/*,*/*;q=0.1',
                    'Accept-Encoding': 'identity',  # Don't use compression to avoid issues
                }
                
                # Make request with streaming
                response = requests.get(audio_url, headers=headers, stream=True, timeout=30)
                response.raise_for_status()
                
                # Check content type
                content_type = response.headers.get('content-type', '').lower()
                if 'audio' not in content_type and 'application/octet-stream' not in content_type:
                    logger.warning(f"Unexpected content type: {content_type}")
                
                # Download file
                total_size = int(response.headers.get('content-length', 0))
                downloaded_size = 0
                
                with open(output_file_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            downloaded_size += len(chunk)
                            
                            # Log progress for large files
                            if total_size > 0 and downloaded_size % (1024 * 1024) == 0:  # Every MB
                                progress = (downloaded_size / total_size) * 100
                                logger.debug(f"Download progress: {progress:.1f}%")
                
                # Verify file was downloaded
                if os.path.exists(output_file_path) and os.path.getsize(output_file_path) > 0:
                    logger.info(f"Successfully downloaded: {output_file_path} ({downloaded_size} bytes)")
                    return output_file_path
                else:
                    raise Exception("Downloaded file is empty or doesn't exist")
                
            except Exception as e:
                logger.warning(f"Download attempt {attempt + 1} failed: {str(e)}")
                
                # Clean up partial file
                if os.path.exists(output_file_path):
                    try:
                        os.remove(output_file_path)
                    except:
                        pass
                
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 2  # Exponential backoff
                    logger.info(f"Retrying in {wait_time} seconds...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"Failed to download after {max_retries} attempts")
                    
        return None
        
    except Exception as e:
        logger.error(f"Failed to download podcast audio {audio_url}: {str(e)}")
        return None


# Test functions if running this module directly
if __name__ == "__main__":
    # Test with a real podcast RSS feed
    test_rss_urls = [
        "https://feeds.simplecast.com/BqbsxVfO",  # The Daily (NY Times)
        "https://rss.cnn.com/rss/edition.rss",  # CNN (might not be podcast)
    ]
    
    print("Testing podcast parser functions:")
    
    for rss_url in test_rss_urls:
        print(f"\n--- Testing with: {rss_url} ---")
        
        # Test get_podcast_info
        print("Getting podcast info...")
        podcast_info = get_podcast_info(rss_url)
        print(f"Podcast: {podcast_info['title']}")
        print(f"Episodes: {podcast_info['total_episodes']}")
        print(f"Author: {podcast_info['author']}")
        
        # Test parse_podcast_rss (limit to first 2 episodes)
        print("Parsing episodes...")
        episodes = parse_podcast_rss(rss_url, start_index=1, end_index=2)
        
        for i, episode in enumerate(episodes, 1):
            print(f"Episode {i}: {episode['title']}")
            print(f"  URL: {episode['audio_url']}")
            print(f"  Date: {episode['pub_date']}")
            print(f"  Duration: {episode['duration']}")
            
            # Test download (commented out to avoid actual downloads in test)
            # if episode['audio_url']:
            #     print(f"  Testing download...")
            #     result = download_podcast_audio(episode['audio_url'], "./test_downloads", episode['title'])
            #     print(f"  Download result: {result}")
            
        if not episodes:
            print("No episodes found or not a valid podcast feed") 