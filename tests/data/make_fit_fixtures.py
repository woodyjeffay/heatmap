"""Regenerates the FIT fixtures in this folder: python make_fit_fixtures.py . (needs fit-tool)."""
import sys, math, datetime
from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.event_message import EventMessage
from fit_tool.profile.messages.activity_message import ActivityMessage
from fit_tool.profile.profile_type import FileType, Manufacturer, Sport, SubSport, Event, EventType

def make(path, sport, n=200, with_gps=True):
    b = FitFileBuilder(auto_define=True, min_string_size=50)
    t0 = round(datetime.datetime(2024,5,1,7,0,tzinfo=datetime.timezone.utc).timestamp()*1000)
    m = FileIdMessage(); m.type = FileType.ACTIVITY; m.manufacturer = Manufacturer.GARMIN.value; m.product = 3113; m.time_created = t0; m.serial_number=1234
    b.add(m)
    e = EventMessage(); e.event = Event.TIMER; e.event_type = EventType.START; e.timestamp = t0; b.add(e)
    for i in range(n):
        r = RecordMessage(); r.timestamp = t0 + i*1000
        if with_gps and i % 17 != 5:   # some records without GPS, like real files
            r.position_lat = 51.5 + 0.0001*i; r.position_long = -0.1 + 0.00005*math.sin(i/10)
        r.heart_rate = 140; r.distance = i*3.0
        b.add(r)
    lap = LapMessage(); lap.timestamp = t0+n*1000; lap.start_time = t0; lap.total_elapsed_time = n; b.add(lap)
    s = SessionMessage(); s.timestamp = t0+n*1000; s.start_time = t0; s.sport = sport; s.sub_sport = SubSport.GENERIC; s.total_elapsed_time=n; b.add(s)
    a = ActivityMessage(); a.timestamp = t0+n*1000; a.num_sessions = 1; b.add(a)
    b.build().to_file(path)

make(sys.argv[1]+"/run.fit", Sport.RUNNING)
make(sys.argv[1]+"/generic.fit", Sport.GENERIC)
make(sys.argv[1]+"/ride.fit", Sport.CYCLING)
make(sys.argv[1]+"/UPPER.FIT", Sport.RUNNING)
make(sys.argv[1]+"/treadmill.fit", Sport.RUNNING, with_gps=False)
