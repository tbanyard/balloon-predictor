"""Validate portable prediction results before registering them for exports."""
import math
from datetime import datetime
from .errors import PredictorError
from .models import PredictionRequest


def validate_result(value):
    def point(p):
        if not isinstance(p,dict):raise ValueError()
        for k in ('latitude','longitude','altitude'):
            if not isinstance(p.get(k),(int,float)) or not math.isfinite(p[k]):raise ValueError()
        if abs(p['latitude'])>90 or abs(p['longitude'])>10000 or not -10000<=p['altitude']<=100000:raise ValueError()
        dt=datetime.fromisoformat(p['datetime'].replace('Z','+00:00'))
        if dt.tzinfo is None:raise ValueError()
    def track(data):
        summary=data['summary'];point(summary['launch']);point(summary['burst'])
        if summary.get('landing'):point(summary['landing'])
        stages=data['prediction']
        if not isinstance(stages,list) or not 1<=len(stages)<=2:raise ValueError()
        for stage in stages:
            if stage['stage'] not in ('ascent','descent') or not isinstance(stage['trajectory'],list) or not 1<=len(stage['trajectory'])<=250000:raise ValueError()
            for p in stage['trajectory']:point(p)
    def flight(data):
        PredictionRequest.model_validate(data['request']);track(data)
        if not isinstance(data['weather'],dict) or not isinstance(data['warnings'],list):raise ValueError()
        if len(data.get('ensemble',[]))>100:raise ValueError()
        for member in data.get('ensemble',[]):track(member)
        # These are receipts, never supplied file contents. Exporters also
        # check them against the source before accessing registered weather.
        for entry in data.get('input_manifest',[]):
            if not isinstance(entry['path'],str) or len(entry['path'])>4096 or not isinstance(entry['size'],int) or not isinstance(entry['mtime_ns'],int):raise ValueError()
    try:
        if not isinstance(value,dict):raise ValueError()
        if 'flights' in value:
            if not isinstance(value['flights'],list) or not 1<=len(value['flights'])<=1000:raise ValueError()
            seen=set()
            for entry in value['flights']:
                if not isinstance(entry['index'],int) or not isinstance(entry['site_index'],int) or entry['index'] in seen:raise ValueError()
                seen.add(entry['index'])
                if entry['status']=='completed':flight(entry['result'])
                elif entry['status']!='failed':raise ValueError()
        else:flight(value)
    except (ValueError,KeyError,TypeError,AttributeError,OverflowError) as error:
        raise PredictorError('Invalid saved flight results. Open an original BalloonPredictor .bpflight file.') from error
    return value
