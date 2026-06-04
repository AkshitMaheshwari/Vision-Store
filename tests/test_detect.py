import numpy as np
import cv2
import pytest
from pipeline.detect import is_wearing_uniform

def test_is_wearing_uniform_red_magenta():
    # Create a mock frame (100x100 BGR)
    # Inside the torso region, fill it with reddish-pink / magenta color
    # Magenta in BGR is (B=180, G=0, R=255)
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    
    # Bounding box of the person
    bbox = (10, 10, 90, 90)
    # Torso region:
    # h = 80, w = 80
    # torso_y1 = 10 + 12 = 22, torso_y2 = 10 + 40 = 50
    # torso_x1 = 10 + 16 = 26, torso_x2 = 10 + 64 = 74
    # Set this region to magenta (e.g. BGR: 120, 20, 220)
    frame[22:50, 26:74] = [120, 20, 220]
    
    assert is_wearing_uniform(frame, bbox) is True

def test_is_wearing_uniform_other_color():
    # Create a mock frame and fill the torso region with black or blue
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    bbox = (10, 10, 90, 90)
    
    # Torso is black (0, 0, 0)
    frame[22:50, 26:74] = [0, 0, 0]
    assert is_wearing_uniform(frame, bbox) is False
    
    # Torso is green (0, 255, 0)
    frame[22:50, 26:74] = [0, 255, 0]
    assert is_wearing_uniform(frame, bbox) is False
