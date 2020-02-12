
'''Utility functions for building the ESG data file from API data

Typical use:
>from extract_esg import *
>extract_esg_data('data/esg_metadata.csv', 'data/ESG_wdi.csv')
>write_feather_file('data/ESG_wdi.csv', 'data/ESG_wdi.feather')
'''
import csv
import wbgapi as wb
import pandas as pd
import numpy as np
import sys
import warnings

def extract_esg_data(metafile_path, csv_path, progress=True):
    '''Extract ESG data from the API and save to CSV file. set progress=False
    to suppress progress messages being written to stderr

    '''

    # for now, we limit countries to the WDI set. Could also limit to the ESG set which is smaller
    economy_filter = [row['id'] for row in wb.economy.list(skipAggs=True)]

    # limit to 1990 on
    time_range = range(1990, 2051)

    with open(metafile_path, 'r') as meta_fd, open(csv_path, 'w') as csv_fd:
        reader = csv.reader(meta_fd)
        writer = csv.writer(csv_fd)

        writer.writerow(['db', 'iso3c', 'date', 'value', 'indicatorID', 'indicator', 'iso2c', 'country'])

        for row in reader:
            (cets,db) = row[0:2]
            if not db.isnumeric():
                continue # could be header or something not in the API

            if int(db) == 11 or int(db) == 57:
                continue    # archived databases

            wb.db = db
            try:
                if progress:
                    print('Fetching {} ({})'.format(cets, db), file=sys.stderr)

                for elem in wb.data.fetch(cets, time=time_range, skipAggs=True, skipBlanks=True, labels=True):
                    if elem['economy']['id'] in economy_filter:
                        # writer.writerow([db, elem['series'], elem['economy'], elem['time'], elem['value']])
                        writer.writerow([db, elem['economy']['id'],
                            elem['time']['value'],
                            elem['value'],
                            elem['series']['id'],
                            elem['series']['value'],
                            wb.economy.iso2(elem['economy']['id']),
                            elem['economy']['value']])
            except wb.APIError as err:
                warnings.warn('ERROR {} ({})\n{}'.format(cets, db, err), RuntimeWarning)


def write_feather_file(csv_path, feather_path):
    '''Convert CSV file on disk to feather
    '''
    esg = pd.read_csv(csv_path, keep_default_na=False, na_values='')
    # these next 2 conversions suppress some notices when R reads the feather file
    esg['db'] = esg['db'].astype('float64')
    esg['date'] = esg['date'].astype('float64')
    esg.to_feather(feather_path)


def esg_prepare(metafile_path, datafile_path):
    '''Loads the metadata file and dynamically calculates the expl_ variables
    based on business logic and the contents of the database
    '''

    # some helpful lists of columns
    expl_a_g = list(map(lambda x: 'expl_'+x, list('abcdefg')))
    expl_c_g = list(map(lambda x: 'expl_'+x, list('cdefg')))

    meta = pd.read_csv(metafile_path)
    data = pd.read_feather(datafile_path)
    
    ### TEMPORARY HACK ###
    # Sudan has bogus values past 2014 for these 2 indicators, so we must delete them manually
    data.drop(index=data[(data.indicatorID=='EN.ATM.CO2E.PC') & (data.date>2014)].index, inplace=True)
    data.drop(index=data[(data.indicatorID=='ER.H2O.INTR.PC') & (data.date>2014)].index, inplace=True)
    ### END TEMPORARY HACK ###

    # calculate no_pop, defined as any indicator with a value for 2018 or later for 90%+ of economies
    min_economies = len(data.iso3c.unique()) * 0.9

    mrv = data[data.date>=2018]

    # dataframe of indicators with counts of countries with at least one MRV
    mrv_series = mrv.groupby(['indicatorID', 'iso3c']).count().groupby('indicatorID').count()
    no_gaps = mrv_series[mrv_series.value>=min_economies]

    # set no_gap for indicators in the previous dataframe
    meta['no_gap'] = meta.join(no_gaps.value, on='cetsid').value.map(lambda x: not np.isnan(x))

    # expl_a is defined as archived. That's databases 11 (Africa Development Indicators) or 57 (WRI archives)
    meta['expl_a'] = (meta.database_id == '11') | (meta.database_id == '57')

    # expl_b is defined as very stale: no values past 2014
    tmp = data[data.date>2014].groupby('indicatorID').count() # count of values AFTER 2014

    # set expl_b for indicators NOT in the previous dataframe (no values after 2014)
    meta['expl_b'] = meta.join(tmp.value, on='cetsid').value.map(lambda x: np.isnan(x))

    # ... but not any archived variables
    meta.loc[meta.expl_a, 'expl_b'] = False

    # ... and not this special case (which is not in the API data)
    meta.loc[meta.cetsid=='WBL', 'expl_b'] = False

    # apply additional business logic. convert 1:0 to booleans
    for row in expl_c_g:
        meta[row] = meta[row].map(lambda x: x==1)
    
    # if no_gap then all expls are false
    meta.loc[meta.no_gap, expl_a_g] = False

    # if a or b then c-g must be false
    meta.loc[meta.expl_a | meta.expl_b, expl_c_g] = False

    return meta
