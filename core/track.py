from __future__ import division
from queue import Empty
import time
import sys
import traceback

from core.constants import CONFIG
from core.files import load_program, save_program, prepare_file
from core.functions import calculate_line, RunningPrograms, find_distance
from core.simple import get_items
from core.messages import *
from core.os import MULTI_MONITOR, monitor_info

if sys.version_info == 2:
    range = xrange
    

def running_processes(q_recv, q_send, background_send):
    """Check for running processes.
    As refreshing the list takes some time but not CPU, this is put in its own thread
    and sends the currently running program to the backgrund process.
    """
    
    previous = None
        
    while True:
            
        received_data = q_recv.get()

        if 'Reload' in received_data:
            try:
                running.reload_file()
            except NameError:
                running = RunningPrograms(queue=q_send)
            NOTIFY(PROGRAM_RELOAD)

        if 'Update' in received_data:
            running.refresh()
            current = running.check()
            if current != previous:
                if current is None:
                    NOTIFY(PROGRAM_QUIT)
                else:
                    NOTIFY(PROGRAM_STARTED, current)
                NOTIFY.send(q_send)
                background_send.put({'Program': current})
                previous = current


def _save_wrapper(q_send, program_name, data, new_program=False):
    
    NOTIFY(SAVE_PREPARE)
    NOTIFY.send(q_send)
    saved = False

    #Get how many attempts to use
    if new_program:
        max_attempts = CONFIG['Save']['MaximumAttemptsSwitch']
    else:
        max_attempts = CONFIG['Save']['MaximumAttemptsNormal']
    
    oompressed_data = prepare_file(data)
    
    #Attempt to save
    NOTIFY(SAVE_START)
    NOTIFY.send(q_send)
    for i in range(max_attempts):
        if save_program(program_name, oompressed_data, compress=False):
            NOTIFY(SAVE_SUCCESS)
            NOTIFY.send(q_send)
            saved = True
            break
        
        else:
            if max_attempts == 1:
                NOTIFY(SAVE_FAIL)
                return
            NOTIFY(SAVE_FAIL_RETRY, CONFIG['Save']['WaitAfterFail'],
                         i, max_attempts)
            NOTIFY.send(q_send)
            time.sleep(CONFIG['Save']['WaitAfterFail'])
            
    if not saved:
        NOTIFY(SAVE_FAIL_END)


def monitor_offset(coordinate, monitor_limits):
    if coordinate is None:
        return
    mx, my = coordinate
    for x1, y1, x2, y2 in monitor_limits:
        if x1 <= mx < x2 and y1 <= my < y2:
            return ((x2 - x1, y2 - y1), (x1, y1))
            
            
def _check_resolution(store, resolution):
    if resolution is None:
        return
    if resolution not in store['Data']['Maps']['Tracks']:
        store['Data']['Maps']['Tracks'][resolution] = {}
    if resolution not in store['Data']['Maps']['Clicks']:
        store['Data']['Maps']['Clicks'][resolution] = [{}, {}, {}]


def background_process(q_recv, q_send):
    """Function to handle all the data from the main thread."""
    try:
        NOTIFY(START_THREAD)
        NOTIFY.send(q_send)
        
        store = {'Data': load_program(),
                 'LastProgram': None,
                 'Resolution': None,
                 'ResolutionTemp': None,
                 'ResolutionList': set(),
                 'Offset': (0, 0),
                 'LastResolution': None,
                 'ActivitySinceLastSave': False,
                 'SavesSkipped': 0,
                 'PingTimeout': CONFIG['Timer']['_Ping'] + 1}
        
        NOTIFY(DATA_LOADED)
        NOTIFY(QUEUE_SIZE, q_recv.qsize())
        NOTIFY.send(q_send)

        maps = store['Data']['Maps']
        
        
        while True:
            
            try:
                received_data = q_recv.get(timeout=store['PingTimeout'])
            except Empty:
                break
                 
            #NOTIFY(MESSAGE_DEBUG, received_data)
            #NOTIFY.send(q_send)
            
            check_resolution = False
            
            if 'Save' in received_data:
                if store['ActivitySinceLastSave']:
                    _save_wrapper(q_send, store['LastProgram'], store['Data'], False)
                    NOTIFY(QUEUE_SIZE, q_recv.qsize())
                    store['ActivitySinceLastSave'] = False
                    store['SavesSkipped'] = 0
                else:
                    store['SavesSkipped'] += 1
                    NOTIFY(SAVE_SKIP, CONFIG['Save']['Frequency'] * store['SavesSkipped'], q_recv.qsize())
                q_send.put({'SaveFinished': None})

            if 'Program' in received_data:
                current_program = received_data['Program']
                
                if current_program != store['LastProgram']:
                    
                    #Check new resolution
                    _check_resolution(store, store['Resolution'])
                    store['ResolutionList'] = set()
                    
                    if current_program is None:
                        NOTIFY(PROGRAM_LOADING)
                    else:
                        NOTIFY(PROGRAM_LOADING, current_program)
                    NOTIFY.send(q_send)
                    
                    #Save old profile
                    _save_wrapper(q_send, store['LastProgram'], store['Data'], True)
                    
                    #Load new profile
                    store['LastProgram'] = current_program
                    store['Data'] = load_program(current_program)
                    maps = store['Data']['Maps']
                    store['ActivitySinceLastSave'] = False
                        
                    if store['Data']['Ticks']['Total']:
                        NOTIFY(DATA_LOADED)
                    else:
                        NOTIFY(DATA_NOTFOUND)
                            
                    NOTIFY(QUEUE_SIZE, q_recv.qsize())
                NOTIFY.send(q_send)

            if 'Resolution' in received_data:
                store['Resolution'] = received_data['Resolution']
                _check_resolution(store, store['Resolution'])
            
            if 'MonitorLimits' in received_data:
                store['ResolutionTemp'] = received_data['MonitorLimits']
            
            #Record key presses
            if 'KeyPress' in received_data:
                store['ActivitySinceLastSave'] = True
                
                for key in received_data['KeyPress']:
                    try:
                        store['Data']['Keys']['Pressed'][key] += 1
                    except KeyError:
                        store['Data']['Keys']['Pressed'][key] = 1
            
            #Record time keys are held down
            if 'KeyHeld' in received_data:
                store['ActivitySinceLastSave'] = True
                
                for key in received_data['KeyHeld']:
                    try:
                        store['Data']['Keys']['Held'][key] += 1
                    except KeyError:
                        store['Data']['Keys']['Held'][key] = 1
            
            #Calculate and track mouse movement
            if 'MouseMove' in received_data:
                store['ActivitySinceLastSave'] = True
                
                start, end = received_data['MouseMove']
                #distance = find_distance(end, start)
                
                #Calculate the pixels in the line
                if end is None:
                    raise TypeError('debug - mouse moved without coordinates')
                if start is None:
                    mouse_coordinates = [end]
                else:
                    mouse_coordinates = [start, end] + calculate_line(start, end)

                #Write each pixel to the dictionary
                for pixel in mouse_coordinates:
                    if MULTI_MONITOR:
                    
                        try:
                            resolution, offset = monitor_offset(pixel, store['ResolutionTemp'])
                        except TypeError:
                            store['ResolutionTemp'] = monitor_info()
                            try:
                                resolution, offset = monitor_offset(pixel, store['ResolutionTemp'])
                            except TypeError:
                                continue
                        
                        pixel = (pixel[0] - offset[0], pixel[1] - offset[1])
                        if resolution not in store['ResolutionList']:
                            _check_resolution(store, resolution)
                            store['ResolutionList'].add(resolution)
                            
                    else:
                        resolution = store['Resolution']
                        
                    maps['Tracks'][resolution][pixel] = store['Data']['Ticks']['Current']['Tracks']
                
                store['Data']['Ticks']['Current']['Tracks'] += 1
                
                #Compress tracks if the count gets too high
                if store['Data']['Ticks']['Current']['Tracks'] > CONFIG['CompressMaps']['TrackMaximum']:
                    compress_multplier = CONFIG['CompressMaps']['TrackReduction']
                    NOTIFY(MOUSE_COMPRESS_START, 'track')
                    NOTIFY.send(q_send)
                    
                    tracks = maps['Tracks']
                    for resolution in tracks.keys():
                        tracks[resolution] = {k: int(v // compress_multplier)
                                              for k, v in get_items(tracks[resolution])}
                        tracks[resolution] = {k: v for k, v in get_items(tracks[resolution]) if v}
                        if not tracks[resolution]:
                            del tracks[resolution]
                            
                    NOTIFY(MOUSE_COMPRESS_END, 'track')
                    NOTIFY(QUEUE_SIZE, q_recv.qsize())
                    store['Data']['Ticks']['Current']['Tracks'] //= compress_multplier
                    store['Data']['Ticks']['Session']['Current'] //= compress_multplier

            #Record mouse clicks
            if 'MouseClick' in received_data:
                store['ActivitySinceLastSave'] = True
                
                for mouse_button, pixel in received_data['MouseClick']:
                    if MULTI_MONITOR:
                    
                        try:
                            resolution, offset = monitor_offset(pixel, store['ResolutionTemp'])
                        except TypeError:
                            store['ResolutionTemp'] = monitor_info()
                            try:
                                resolution, offset = monitor_offset(pixel, store['ResolutionTemp'])
                            except TypeError:
                                continue
                            
                        pixel = (pixel[0] - offset[0], pixel[1] - offset[1])
                        
                    else:
                        resolution = store['Resolution']
                        
                    try:
                        maps['Clicks'][resolution][mouse_button][pixel] += 1
                    except KeyError:
                        maps['Clicks'][resolution][mouse_button][pixel] = 1
                
            #Increment the amount of time the script has been running for
            if 'Ticks' in received_data:
                store['Data']['Ticks']['Total'] += received_data['Ticks']
            store['Data']['Ticks']['Recorded'] += 1

            NOTIFY.send(q_send)
        
        #Exit process (this shouldn't happen for now)
        NOTIFY(THREAD_EXIT)
        NOTIFY.send(q_send)
        _save_wrapper(q_send, store['LastProgram'], store['Data'], False)
            
    except Exception as e:
        q_send.put(traceback.format_exc())
        return
