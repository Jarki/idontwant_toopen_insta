import pytest

from ig_reel_downloader import utils

@pytest.mark.parametrize('url, expected', [
    ('https://www.instagram.com/reel/DFvr8JTuscr', ['https://www.instagram.com/reel/DFvr8JTuscr']),
    ('No link here!', []),
    ('https://www.instagram.com/reel/DGcc6bHIKFY\n\nhttps://www.instagram.com/reel/DFvr8JTuscr', ['https://www.instagram.com/reel/DGcc6bHIKFY', 'https://www.instagram.com/reel/DFvr8JTuscr']),
    ('https://www.instagram.com/reel/DGcc6bHIKFY\n\nhttps://www.instagram.com/reel/DFvr8JTuscr And some random text',
      ['https://www.instagram.com/reel/DGcc6bHIKFY', 'https://www.instagram.com/reel/DFvr8JTuscr']),
    ('https://www.instagram.com/reel/DJcm-GGRTJq', ['https://www.instagram.com/reel/DJcm-GGRTJq'])
])
def test_get_urls_from_text(url, expected):
    assert utils.get_urls_from_text(url) == expected

@pytest.mark.parametrize('url, expected', [
    ('https://www.instagram.com/reel/DFvr8JTuscr', 'DFvr8JTuscr'),
    ('https://www.instagram.com/reel/DGcc6bHIKFY', 'DGcc6bHIKFY'),
    ('https://www.instagram.com/reel/DJcm-GGRTJq/', 'DJcm-GGRTJq'),
    ('https://www.instagram.com/reel/D', 'D'),
    ('https://www.instagram.com/reel/D/', 'D'),
    ('https://www.instagram.com/reel/D?', 'D'),
    ('https://www.instagram.com/reel/D/123/456/', 'D'),
    ('https://www.instagram.com/reel/D/123/456/789/', 'D'),
])
def test_get_id_from_url(url, expected):
    assert utils.get_id_from_url(url) == expected