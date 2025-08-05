from sweet.ui.widgets import ExcelDataGrid

def test_cities_table():
    cities_table = """Rank (2021)	Municipality	Province	Municipal status	Population (2021)	Population (2016)	Change	Land area (km2)	Population density (/km2)
1
Toronto	Ontario	City	2,794,356	2,731,571	+2.3%	631.1	4,427.8
2
Montreal	Quebec	Ville	1,762,949	1,704,694	+3.4%	364.74	4,833.4
3
Calgary	Alberta	City	1,306,784	1,239,220	+5.5%	820.62	1,592.4
4
Ottawa	Ontario	City	1,017,449	934,243	+8.9%	2788.2	364.9
5
Edmonton	Alberta	City	1,010,899	933,088	+8.3%	765.61	1,320.4
6
Winnipeg	Manitoba	City	749,607	705,244	+6.3%	461.78	1,623.3
7
Mississauga	Ontario	City	717,961	721,599	−0.5%	292.74	2,452.6
8
Vancouver	British Columbia	City	662,248	631,486	+4.9%	115.18	5,749.7
9
Brampton	Ontario	City	656,480	593,638	+10.6%	265.89	2,469.0
10
Hamilton	Ontario	City	569,353	536,917	+6.0%	1118.31	509.1"""
        
    widget = ExcelDataGrid()
    result = widget._parse_clipboard_data(cities_table)
    assert result is not None
    assert result['num_cols'] == 9
    first_data_row = result['rows'][1] if result['has_headers'] else result['rows'][0]
    assert 'Toronto' in first_data_row
    assert 'Ontario' in first_data_row

    
def test_cities_table_2():
    us_cities_table = """	2020 density	Location
mi2	km2	/ mi2	/ km2
New York[c]	NY	8,478,072	8,804,190	−3.70%	300.5	778.3	29,298	11,312	40.66°N 73.94°W
Los Angeles	CA	3,878,704	3,898,747	−0.51%	469.5	1,216.0	8,304	3,206	34.02°N 118.41°W
Chicago	IL	2,721,308	2,746,388	−0.91%	227.7	589.7	12,061	4,657	41.84°N 87.68°W
Houston	TX	2,390,125	2,304,580	+3.71%	640.4	1,658.6	3,599	1,390	29.79°N 95.39°W
Phoenix	AZ	1,673,164	1,608,139	+4.04%	518.0	1,341.6	3,105	1,199	33.57°N 112.09°W
Philadelphia[d]	PA	1,573,916	1,603,797	−1.86%	134.4	348.1	11,933	4,607	40.01°N 75.13°W
San Antonio	TX	1,526,656	1,434,625	+6.41%	498.8	1,291.9	2,876	1,110	29.46°N 98.52°W
San Diego	CA	1,404,452	1,386,932	+1.26%	325.9	844.1	4,256	1,643	32.81°N 117.14°W
Dallas	TX	1,326,087	1,304,379	+1.66%	339.6	879.6	3,841	1,483	32.79°N 96.77°W
Jacksonville[e]	FL	1,009,833	949,611	+6.34%	747.3	1,935.5	1,271	491	30.34°N 81.66°W"""
        
    widget = ExcelDataGrid()
    result = widget._parse_clipboard_data(us_cities_table)
    assert result is not None
    assert result['num_rows'] > 0
    
def test_table_with_line_breaks_in_headers():
    table_with_breaks = """Country	Population
(millions)	GDP
(trillion USD)	Area
(million km²)
China	1439.3	17.7	9.6
India	1380.0	3.4	3.3
United States	331.0	23.3	9.8"""
        
    widget = ExcelDataGrid()
    result = widget._parse_clipboard_data(table_with_breaks)
    assert result is not None
    assert result['num_rows'] > 0
    
def test_table_with_spanning_headers():
    spanning_headers_table = """Rank	City	Population	Area	Density
		2020	2010	km²	mi²	/km²	/mi²
1	Tokyo	37,833,000	36,923,000	2,188	845	17,298	44,802
2	Delhi	30,291,000	22,654,000	1,484	573	20,411	52,864
3	Shanghai	27,058,000	20,860,000	6,341	2,448	4,267	11,052"""
        
    widget = ExcelDataGrid()
    result = widget._parse_clipboard_data(spanning_headers_table)
    assert result is not None
    assert result['num_rows'] > 0
    
def test_table_with_footnotes():
    footnotes_table = """Country	Capital	Population[a]	GDP[b]
France	Paris[c]	67,391,582	2,938
Germany	Berlin	83,166,711	4,223
Italy	Rome[d]	60,317,116	2,107"""
        
    widget = ExcelDataGrid()
    result = widget._parse_clipboard_data(footnotes_table)
    assert result is not None
    assert result['num_rows'] > 0
    
def test_empty_cells_and_irregular_structure():
    irregular_table = """Name	Age	City	Country
John	25		USA
	30	London	UK
Sarah		Paris	France
Mike	35	Tokyo	"""
        
    widget = ExcelDataGrid()
    result = widget._parse_clipboard_data(irregular_table)
    assert result is not None
    assert result['num_rows'] > 0
    
def test_movies_table_with_title():
    movies_table = """Highest-grossing films of 2025[12][13]
Rank	Title	Distributor	Worldwide gross
1	Ne Zha 2	Beijing Enlight	$2,217,080,000
2	Lilo & Stitch †	Disney	$1,019,581,728
3	A Minecraft Movie	Warner Bros.	$955,149,195
4	Jurassic World Rebirth †	Universal	$766,011,000
5	How to Train Your Dragon †		$618,347,000
6	Mission: Impossible – The Final Reckoning †	Paramount	$594,218,706
7	Superman †	Warner Bros.	$551,256,392
8	F1 †	Warner Bros. / Apple	$546,291,000
9	Detective Chinatown 1900	Wanda	$503,214,752[14]
10	Captain America: Brave New World	Disney	$415,101,577"""
        
    widget = ExcelDataGrid()
    result = widget._parse_clipboard_data(movies_table)
    assert result is not None
    expected_cols = 4
    assert result['num_cols'] == expected_cols
    
def test_buildings_table_with_empty_header():
    buildings_table = """	Name	Height[14]	Floors	Image	City	Country	Year	Comments	Ref
m	ft
1	Burj Khalifa	828	2,717	163 (+ 2 below ground)		Dubai	 United Arab Emirates	2010	Tallest building in the world since 2009	[15]
2	Merdeka 118	678.9	2,227	118 (+ 5 below ground)		Kuala Lumpur	 Malaysia	2024	Tallest building in Southeast Asia	[16]
3	Shanghai Tower	632	2,073	128 (+ 5 below ground)		Shanghai	 China	2015	Tallest building in East Asia, tallest twisted building in the world; contains the highest luxury hotel in the world	[17]
4	The Clock Towers	601	1,972	120 (+ 3 below ground)		Mecca	 Saudi Arabia	2012	Tallest building in Saudi Arabia, tallest clock tower and contains the highest museum in the world	[18]"""
        
    widget = ExcelDataGrid()
    result = widget._parse_clipboard_data(buildings_table)
    assert result is not None
    assert result['has_headers'] == True
    
def test_whales_table_multiline_headers():
    whales_table = """Rank	Animal	Average mass
[tonnes]	Maximum mass
[tonnes]	Average total length
[m (ft)]
1	Blue whale[15]	110[16]	190[1]	24 (79)[17]
2	North Pacific right whale	60[18]	120[1]	15.5 (51)[16]
3	Southern right whale	58[16]	110[19]	15.25 (50)[16]
4	Fin whale	57[16]	120[19][20]	19.5 (64)[16]
5	Bowhead whale	54.5[16][21]	120[1]	15 (49)[16]
6	North Atlantic right whale	54[16][22]	110[19][23]	15 (49)[16][23]
7	Sperm whale	31.25[16][24]	57[25]	13.25 (43.5)[16][24]
8	Humpback whale	29[16][26]	48[27]	13.5 (44)[16]
9	Sei whale	22.5[16]	45[28]	14.8 (49)[16]
10	Gray whale	19.5[16]	45[29]	13.5 (44)[16]"""
        
    widget = ExcelDataGrid()
    result = widget._parse_clipboard_data(whales_table)
    assert result is not None
    expected_cols = 5
    assert result['num_cols'] == expected_cols
    
def test_reptiles_table_multiline_headers():
    reptiles_table = """Rank	Animal	Average mass
[kg (lb)]	Maximum mass
[kg (lb)]	Average total length
[m (ft)]
1	Saltwater crocodile	450 (1,000)[92][93]	2,000 (4,409 lbs)[94][95]	4.5 (14.8)[92][96]
2	Nile crocodile	410 (900)[97]	1,090 (2,400)[1]	4.2 (13.8)[97]
3	Orinoco crocodile	380 (840)[citation needed]	1,100 (2,400)[citation needed]	4.1 (13.5)[98][99]
4	Leatherback sea turtle	364 (800)[100][101]	932 (2,050)[1]	2.0 (6.6)[1]
5	American crocodile	336 (740)[102]	1,000 (2,200)[103]	4.0 (13.1)[104][105]"""
        
    widget = ExcelDataGrid()
    result = widget._parse_clipboard_data(reptiles_table)
    assert result is not None
    assert result['num_rows'] > 0
    
def test_countries_table_missing_structure():
    countries_table = """Common and formal names	Membership within the UN System[c]	Sovereignty dispute[d]	Further information on status and recognition of sovereignty[f]
 Afghanistan – Islamic Emirate of Afghanistan	UN member state	None	The ruling Islamic Emirate of Afghanistan, in power since 2021, has not been recognised by the United Nations or any other state except Russia.[5] The defunct  Islamic Republic of Afghanistan remains the recognised government.[6][7]
 Albania – Republic of Albania	UN member state	None	
 Algeria – People's Democratic Republic of Algeria	UN member state	None	
 Andorra – Principality of Andorra	UN member state	None	Andorra is a co-principality in which the office of head of state is jointly held ex officio by the French president and the bishop of the Roman Catholic diocese of Urgell,[8] who himself is appointed with approval from the Holy See.
 Angola – Republic of Angola	UN member state	None	
 Antigua and Barbuda	UN member state	None	Antigua and Barbuda is a Commonwealth realm[g] with one autonomous region, Barbuda.[9][h]
 Argentina – Argentine Republic[i]	UN member state	None	Argentina is a federation of 23 provinces and one autonomous city.[j]
 Armenia – Republic of Armenia	UN member state	Not recognised by Pakistan	Armenia is not recognised by Pakistan due to the dispute over Artsakh.[11][12][13][needs update]"""
        
    widget = ExcelDataGrid()
    result = widget._parse_clipboard_data(countries_table)
    assert result is not None
    assert result['num_rows'] > 0
