"""
Meta profiles -- GENERATED, do not edit by hand.

Everything about each project's `meta` sheet that the universal rules in
meta_spec.py cannot know by themselves: its crop families, the shape and
vocabulary of each family's activityName, the ISIC code per family, the
constant column values, and the house wording of each prose column.

Keyed by project, so one run can judge every project at once and
profiling a new project leaves the others intact.

A {n} slot in a template is text the dataset supplies itself (crop noun,
cultivation system, region, yield); everything else is fixed house style.

Written by meta_model/build_meta_spec.py -- re-run it with
--write for a new project, or when a house style changes.
"""

PROFILES = {
    'ILCIDAF project full': {
        'datasets': 129,
        'constants': {
            'dataEntryBy_isActiveAuthor': 'True',
            'dataGeneratorAndPublication_accessRestrictedTo': 'Licensees',
            'specialActivityType': 'ordinary transforming activity',
            'technologyLevel': 'Current',
        },
        'always_empty': (
            'synonyms',
            'tags',
            'dataEntryBy_personName',
            'dataGeneratorAndPublication_dataPublishedIn',
            'dataGeneratorAndPublication_pageNumbers',
            'dataGeneratorAndPublication_personName',
            'dataGeneratorAndPublication_publishedSourceId',
            'modellingAndValidation_percent',
            'fileAttributes_majorRelease',
            'fileAttributes_minorRelease',
            'fileAttributes_majorRevision',
            'fileAttributes_minorRevision',
        ),
        'regions': ('centre', 'islands', 'north east', 'north west', 'south'),
        'families': {
            'clementine production': {
                'datasets': 8,
                'isic': '0123:Growing of citrus fruits',
                'fixed_segments': 3,
                'optional_last': True,
                'segment_counts': [3, 4],
                'open_axes': [False, False, False],
                'axis_labels': ['production_system', 'water_regime', 'grade'],
                'optional_label': 'region',
                'optional_vocabulary': ['centre', 'islands', 'north west', 'south'],
                'vocabulary': [
                    ['conventional', 'integrated', 'organic'],
                    ['irrigated'],
                    ['fresh grade'],
                ],
                'templates': {
                    'generalComment': [
                        'This dataset represents the average production of 1 kg of {1} obtained under {2} cultivation at farm level to produce {3} fresh grade. This cultivation generates one output: {4} fresh grade. The dataset includes the production of seedlings, planting trees and establishing of orchard, {5} fertilisers (Nitrogen, Phosphorus, Potassium), and plant protection products (herbicides, fungicides, and insecticides). It encompasses all mechanical operations carried out with agricultural machinery, from soil tillage and sowing to fertilisation, pest control treatments, irrigation, and transport to the farm centre. This production is modelled as a irrigated system. Modelled crop duration: 25 years, but the first 3 years are unproductive. Yield: {6} kg/ha.',
                    ],
                    'geographyComment': [
                        'Data on {1} clementine grove cultivation represent {2} region (southern Italy). Data on fertilisers (based on nitrogen, phosphorus and potassium) refer to Italy (national average). Data on fuel, lubricating oil, anti-cryptogamics, herbicides and applied technologies refer to the plains of southern Italy.',
                        'Data on integrated {1} grove cultivation represent {2} Italy. Data on fertilisers (based on nitrogen, phosphorus and potassium), fuel, lubricating oil, anti-cryptogamics, herbicides and applied technologies refer to {3}',
                    ],
                    'includedActivitiesEnd': [
                        'This activity ends after harvesting the clementine.',
                        'This activity ends after harvesting the oranges.',
                    ],
                    'includedActivitiesStart': [
                        'This activity starts with soil preparation before planting the fruit tree seeds.',
                    ],
                    'modellingAndValidation_extrapolations': [
                        'The agricultural phase of {1} clementine {2} in {3} is derived from {4} data. The analysis of production and cultivated area, and the relative production yield are the result of average data for the time period 2015-2020. Fertilisers application rates (in terms of N, P and K) are derived from {5} Materials such as fuel, lubricating oil, pesticides, herbicides are taken from {6} The market values of the product is an average of the price quotations given in ISMEA database. The emissions due to fertilisers use are accorded to IPCC Report, 2006 and the JRC Report "Zampori and Pant, 2019". The emissions due to pesticides use are calculated according to the OLCA-Pest model (Nemecek et al. 2022). Emissions of heavy metals are estimated according to what is indicated in the SALCA heavy metals model (Freiermuth, 2006), however the data have been regionalised for the Italian territory according to the ESDAC database (Notarnicola et al., 2023). Clementines production value is the result of {7} average {8} data {9} For the estimation of heavy metals, the SALCA heavy metals model is followed. The erosion and leaching of heavy metals is determined following the identification of local data on land configuration, soil type, the physical and chemical characteristics of the soils, and the activities carried out for the cultivation of citrus. The heavy metals for which emissions have been estimated are: Cd, Cu, Cr, Zn, Ni, Pb, Hg.',
                    ],
                    'technologyComment': [
                        'The reference technology is evaluated using data collected on field. The process describes the average production of clementine, fresh grade, cultivated in one hectare of land in {1} region (Southern Italy). The activities considered include: fruit tree seedling production for planting, planting of trees and establishment of the orchard, followed by full production clementine grove management. The annual management of the clementine grove includes: soil preparation through rotary cultivation and harrowing by rotary harrow; drip irrigation; mineral fertilisation with {2} nitrogen (as N), {3} phosphorus (as P₂O₅) and {4} potassium (as K₂O), applied by broadcaster; weed and pest control through the application of plant protection products by field sprayer. Finally, the system includes manual pruning and manual harvesting of the fruit and transport of the products to the farm center. The consumption of fuel and lubricating oil are derived from data collected on field. The {5} fertilisers used are N, P2O5 and K2O. The {6} insecticides used are {7}',
                        'The reference technology is evaluated using data collected from statistical sources. The process describes the average production of clementines, fresh grade, cultivated in one hectare of land in {1} Italy. The activities considered include: fruit tree seedling production for planting, planting of trees and establishment of the orchard, followed by full production clementine grove management. The annual management of the clementine grove includes: soil preparation through rotary cultivation and harrowing by rotary harrow; drip irrigation; mineral fertilisation with inorganic nitrogen (as N), inorganic phosphorus (as P₂O₅) and inorganic potassium (as K₂O), applied by broadcaster; weed and pest control through the application of plant protection products by field sprayer. Finally, the system includes manual pruning and manual harvesting of the fruit and transport of the products to the farm center. The consumption of fuel and lubricating oil are derived from Ribaudo (2017). The chemical fertilisers used are N, P2O5 and K2O. The herbicides used are Carfentrazone, Diflufenican. The fungicide used is copper oxychloride. The insecticides used are Mancozeb, Pyriproxifen, Phosmet, Azadirachtin, Mineral oil.',
                    ],
                    'timePeriodComment': [
                        'Data were collected in the field between 2020 and 2023. Production per hectare represents the average of the past three years for taking into account the yearly fluctuations of {1} production.',
                    ],
                },
            },
            'grape production': {
                'datasets': 43,
                'isic': '0121:Growing of grapes',
                'fixed_segments': 4,
                'optional_last': False,
                'segment_counts': [4],
                'open_axes': [True, False, False, False],
                'axis_labels': [None, 'production_system', 'water_regime', 'region'],
                'optional_label': None,
                'optional_vocabulary': [],
                'vocabulary': [
                    ['aglianico del taburno', 'aglianico del vulture', 'asti', 'barbaresco', 'biferno', 'brunello montalcino', 'cabernet sauvignon', 'cannonau', 'castelli romani', 'chianti', 'cinque terre', 'cirò', 'colle della sabina', 'conegliano valdobbiadene', 'conero', 'emilia', 'friuli grave', 'fumin', 'greco di tufo', 'grillo', 'lago caldaro', 'lambrusco mantovano', 'lison pramaggiore', 'montefalco', "montepulciano d'abruzzo", 'nerello mascalese', "nero d'avola", 'orvieto', 'pinot nero', 'primitivo di manduria', 'puglia', 'reggiano lambrusco', 'riesling', 'romagna doc sangiovese superiore', 'romagna predappio doc sangiovese symbiotic wine', 'scavigna', 'terre dell’alta val d’agri', "trebbiano d'abruzzo", 'val polcevara', 'valcalepio', 'valpolicella', 'verdicchio', 'vermentino di sardegna'],
                    ['conventional', 'organic'],
                    ['irrigated'],
                    ['centre', 'islands', 'north east', 'north west', 'south'],
                ],
                'templates': {
                    'generalComment': [
                        'This dataset represents the average production of 1 kg of {1} grapes produced in the {2} region (Italy), obtained under conventional cultivation at farm level to produce grapes that will later be transported to processing {3} dataset includes the application and field distribution of {4} plant protection treatments (fungicides and cryptogam control), and the consumption of {5} and auxiliary materials. It encompasses all mechanical operations carried out with agricultural machinery, from {6} to fertilisation, pest control {7} and {8} Modelled crop duration 25 years, but the first 3 years are unproductive. Yield: {9} kg/ha.',
                    ],
                    'geographyComment': [
                        'The inventory is modelled for the Italian region of {1}',
                        'The inventory is modelled for the Italian region of {1}',
                        'The inventory is modelled for the Italian region of Trentino Alto Adige.',
                        'The inventory is modelled for the Italian region of Friuli Venezia Giulia.',
                        "The inventory is modelled for the Italian region of Valle d'Aosta.",
                        'The inventory is modelled for the Italian region of Emilia Romagna.',
                    ],
                    'includedActivitiesEnd': [
                        'This activity ends after the harvesting of the grapes.',
                    ],
                    'includedActivitiesStart': [
                        'This activity starts with soil preparation before the harvesting of the grapes.',
                    ],
                    'modellingAndValidation_extrapolations': [
                        'The agricultural phase of grape cultivation in the region {1} of Italy is derived from statistical and literature data adapted and corrected for the reference area covered by this data sheet. The analysis of production and cultivated area, and the relative production yield are the result of average data for the time period 2015-2020 derived from the ISTAT database. Fertiliser application rates (in terms of N, P and K) derived from the average of the integrated production specifications of the regions where {2} production occurs, where the maximum legal quantity applicable for one hectare of land cultivated with grapes was considered. Materials such as fuel and lubricating oil and fertilisers are taken from the data in the Agricultural Handbook "Ribaudo, 2017". The emissions due to fertilisers use are according to the IPCC Report, 2006 and the JRC Report "Zampori et al, 2019". The emissions due to pesticides use are calculated according to the OLCA-Pest model (Nemecek et al. 2022). Emissions of heavy metals are estimated according to what is indicated in the SALCA heavy metals model (Freiermuth, 2006), however the data have been regionalised for the Italian territory according to the ESDAC database (Notarnicola et al., 2023). {3} Grapes production values are the result of combining data from Agricultural Handbook "Ribaudo, 2017" and Production Regulations of {4} region, 2022). The data on fuel, lubricating oil consumption, fertilisers and cryptogams are representative of the lowlands of {5} Italy. For the estimation of heavy metals, the SALCA heavy metals model is followed. The erosion and leaching of heavy metals is determined following the identification of local data on land configuration, soil type, the physical and chemical characteristics of the soils, and the activities carried out for the cultivation of {6} grapes. The heavy metals for which emissions have been estimated are: Cd, Cu, Cr, Zn, Ni, Pb, Hg.',
                        'Materials such as fuel, cryptograms and fertilisers are site specific and refer to year 2022. The emissions due to fertilisers use are according to the IPCC Report, 2006 and the JRC Report "Zampori et al, 2019". Plastic packaging end-of-life (fertilisers and pesticides) was assessed by considering recycling (28,0%), energy recovery (41,5%) and disposal (30,5%), according to the "EC 2021 - Annex C of the European Platform on Life Cycle Assessment (OEF) method". The data were all collected in the field at {1} the company. For the estimation of heavy metals, the SALCA heavy metals model is followed. The erosion and leaching of heavy metals is determined following the identification of local data on land configuration, soil type, the physical and chemical characteristics of the soils, and the activities carried out for the cultivation of durum wheat. The heavy metals for which emissions have been estimated are: Cd, Cu, Cr, Zn, Ni, Pb, Hg. The emissions due to pesticides use are calculated according to the OLCA-Pest model (Nemecek et al. 2022).',
                    ],
                    'technologyComment': [
                        'The reference technology was evaluated using literature data. The process describes the average production of {1} grapes in one hectare of land in Italy. The activities considered include: application and distribution of fertilisers (based on nitrogen, phosphorus and potassium), parasites treatment (cryptogam control) as well as the consumption of fuel and lubricating oil for Weeding, Combating cryptogams, Light ploughing, the usage of an Inter-Row Mechanical hoe, Pruning, Trimming, Harvestin and transports. Their usage is derived from data in the Production Regulations of {2} region, 2022) and the Agricultural Handbook (Ribaudo, 2017). The chemical fertilisers include: N, P205 and K2O. The fungicides used are: Benalaxyl, Cymoxanil, Copper oxychloride, Ametoctradin, Famoxadone while sulfur is used as both pesticide and fungicide.',
                        'The reference technology was evaluated using literature data. The process describes the average production of {1} grapes in one hectare of land in Italy. The activities considered include: application and distribution of fertilisers (based on nitrogen, phosphorus and potassium), parasites treatment (cryptogam control) as well as the consumption of fuel for Weeding, Combating cryptogams, Pruning, Trimming, and transports. Their usage is derived from data in the Production Regulations of {2} region, 2022) and the Agricultural Handbook (Ribaudo, 2017). The chemical fertilisers include: N, P205 and K2O. The fungicides used are: Zoxamide, Fluopicolide and Fosetyl-al, Zoxamide, fludioxonil + cyprodinil, Dimethomorph, Penconazole, Copper hydroxide, Wettable sulphur and Glyphosate while sulfur is used as both pesticide and fungicide.',
                        'The reference technology was evaluated using literature data. The process describes the average production of {1} grapes in one hectare of land in Italy. The activities considered include: application and distribution of fertilisers (based on nitrogen, phosphorus and potassium), parasites treatment (cryptogam {2} well as the consumption of fuel for Weeding, Combating cryptogams, Light ploughing, the usage of an Inter-Row Mechanical hoe, Pruning, Trimming, and transports. Their usage is derived from data in the Production Regulations of {3} region, 2022) and the Agricultural Handbook (Ribaudo, 2017). The chemical fertilisers include: N, P205 and K2O. The fungicides used are: Pyraclostrobin + Metiram, Dimethomorph, Copper Hydroxide, Mancozeb, Pulverulent Sulphur, Spinosad while sulfur is used as both pesticide and fungicide.',
                        'The reference technology was evaluated using literature data. The process describes the average production of {1} grapes in one hectare of land in Italy. The activities considered include: application and distribution of fertilisers (based on nitrogen, phosphorus and potassium), parasites treatment (cryptogam control) as well as the consumption of fuel for Weeding, Combating cryptogams, Pruning, Trimming, and {2} Their usage is derived from data in the Production Regulations of {3} region, 2022) and the Agricultural Handbook (Ribaudo, 2017). The chemical fertilisers include: N, P205 and K2O. The fungicides used are: Metiram, Cymoxanil, Mancozeb, Copper Oxychloride, Micronised Sulphur, Pulverulent Sulphur and as an insecticide Chlorpyrifos Methyl is used. Sulfur is used as both pesticide and fungicide.',
                        'The reference technology was evaluated using literature data. The process describes the average production of {1} grapes in one hectare of land in Italy. The activities considered include: application and distribution of fertilisers (based on nitrogen, phosphorus and potassium), parasites treatment (cryptogam control) as well as the consumption of fuel for Weeding, Combating cryptogams, the usage of a Rotary Hoe and transports. Their usage is derived from data in the Production Regulations of {2} region, 2022) and the Agricultural Handbook (Ribaudo, 2017). The chemical fertilisers include: N, P205 and K2O. The fungicides used are: Bordeaux Mixture, Wettable Sulphur and Pulverulent Sulphur. Sulphur is used as both pesticide and fungicide.',
                    ],
                    'timePeriodComment': [
                        'The statistical analysis is conducted over a six-year time period (2015 - 2020), so as to consider average values that take into account changes in agricultural yield.',
                    ],
                },
            },
            'lemon production': {
                'datasets': 9,
                'isic': '0123:Growing of citrus fruits',
                'fixed_segments': 3,
                'optional_last': True,
                'segment_counts': [3, 4],
                'open_axes': [False, False, False],
                'axis_labels': ['production_system', 'water_regime', 'grade'],
                'optional_label': 'region',
                'optional_vocabulary': ['centre', 'islands', 'north west', 'south'],
                'vocabulary': [
                    ['conventional', 'integrated', 'organic'],
                    ['irrigated'],
                    ['fresh grade'],
                ],
                'templates': {
                    'generalComment': [
                        'This dataset represents the average production of 1 kg of {1} obtained under {2} cultivation at farm level to produce lemon, fresh grade. This cultivation generates one output: lemon, fresh grade. The dataset includes the production of seedlings, planting trees and establishing of orchard, {3} fertilisers (Nitrogen, Phosphorus, Potassium), and plant protection products (herbicides, fungicides, and insecticides). It encompasses all mechanical operations carried out with agricultural machinery, from soil tillage and sowing to fertilisation, pest control treatments, irrigation, and transport to the farm centre. This production is modelled as a irrigated system. Modelled crop duration: 25 years, but the first 3 years are unproductive. Yield: {4} kg/ha.',
                    ],
                    'geographyComment': [
                        'Data on {1} lemon grove cultivation represent {2} region (southern Italy). Data on fertilisers (based on nitrogen, phosphorus and potassium) refer to Italy (national average). Data on fuel, lubricating oil, anti-cryptogamics, herbicides and applied technologies refer to the plains of southern Italy.',
                        'Data on integrated lemons grove cultivation represent {1} Italy. Data on fertilisers (based on nitrogen, phosphorus and potassium), fuel, lubricating oil, anti-cryptogamics, herbicides and applied technologies refer to Italy.',
                    ],
                    'includedActivitiesEnd': [
                        'This activity ends after harvesting the lemon.',
                        'This activity ends after harvesting the oranges.',
                    ],
                    'includedActivitiesStart': [
                        'This activity starts with soil preparation before planting the fruit tree seeds.',
                    ],
                    'modellingAndValidation_extrapolations': [
                        'The agricultural phase of {1} lemon {2} in {3} is derived from {4} data. The analysis of production and cultivated area, and the relative production yield are the result of average data for the time period 2015-2020. Fertilisers application rates (in terms of N, P and K) are derived from {5} Materials such as fuel, lubricating oil, pesticides, herbicides are taken from {6} The market values of the product is an average of the price quotations given in ISMEA database. The emissions due to fertilisers use are accorded to IPCC Report, 2006 and the JRC Report "Zampori and Pant, 2019". The emissions due to pesticides use are calculated according to the OLCA-Pest model (Nemecek et al. 2022). Emissions of heavy metals are estimated according to what is indicated in the SALCA heavy metals model (Freiermuth, 2006), however the data have been regionalised for the Italian territory according to the ESDAC database (Notarnicola et al., 2023). {7} production value is the result of {8} average {9} data {10} For the estimation of heavy metals, the SALCA heavy metals model is followed. The erosion and leaching of heavy metals is determined following the identification of local data on land configuration, soil type, the physical and chemical characteristics of the soils, and the activities carried out for the cultivation of citrus. The heavy metals for which emissions have been estimated are: Cd, Cu, Cr, Zn, Ni, Pb, Hg.',
                    ],
                    'technologyComment': [
                        'The reference technology is evaluated using data collected on field. The process describes the average production of lemon, fresh grade, cultivated in one hectare of land in {1} region (Southern Italy). The activities considered include: fruit tree seedling production for planting, planting of trees and establishment of the orchard, followed by full production lemon grove management. The annual management of the lemon grove includes: soil preparation through rotary cultivation and harrowing by rotary harrow; drip irrigation; {2} fertilisation with {3} nitrogen (as N), {4} phosphorus (as P₂O₅) and {5} potassium (as K₂O), applied by broadcaster; weed and pest control through the application of plant protection products by field sprayer. Finally, the system includes manual pruning and manual harvesting of the fruit and transport of the products to the farm center. The consumption of fuel and lubricating oil are derived from data collected on field. The {6} fertilisers used are N, P2O5 and K2O. The {7} insecticides used are {8}',
                        'The reference technology is evaluated using data collected from statistical sources. The process describes the average production of lemons, fresh grade, cultivated in one hectare of land in {1} Italy. The activities considered include: fruit tree seedling production for planting, planting of trees and establishment of the orchard, followed by full production clementine grove management. The annual management of the lemon grove includes: soil preparation through rotary cultivation and harrowing by rotary harrow; drip irrigation; mineral fertilisation with inorganic nitrogen (as N), inorganic phosphorus (as P₂O₅) and inorganic potassium (as K₂O), applied by broadcaster; weed and pest control through the application of plant protection products by field sprayer. Finally, the system includes manual pruning and manual harvesting of the fruit and transport of the products to the farm center. The consumption of fuel and lubricating oil are derived from Ribaudo (2017). The chemical fertilisers used are N, P2O5 and K2O. The herbicides used are Carfentrazone, Diflufenican. The fungicide used is copper oxychloride. The insecticides used are Mancozeb, Pyriproxifen, Phosmet, Azadirachtin, Mineral oil.',
                    ],
                    'timePeriodComment': [
                        'Data were collected in the field between 2020 and 2023. Production per hectare represents the average of the past three years for taking into account the yearly fluctuations of {1} production.',
                    ],
                },
            },
            'mandarin production': {
                'datasets': 8,
                'isic': '0123:Growing of citrus fruits',
                'fixed_segments': 3,
                'optional_last': True,
                'segment_counts': [3, 4],
                'open_axes': [False, False, False],
                'axis_labels': ['production_system', 'water_regime', 'grade'],
                'optional_label': 'region',
                'optional_vocabulary': ['centre', 'islands', 'north west', 'south'],
                'vocabulary': [
                    ['conventional', 'integrated', 'organic'],
                    ['irrigated'],
                    ['fresh grade'],
                ],
                'templates': {
                    'generalComment': [
                        'This dataset represents the average production of 1 kg of mandarins, obtained under {1} cultivation at farm level to produce {2} fresh grade. This cultivation generates one output: {3} fresh grade. The dataset includes the production of seedlings, planting trees and establishing of orchard, {4} fertilisers (Nitrogen, Phosphorus, Potassium), and plant protection products (herbicides, fungicides, and insecticides). It encompasses all mechanical operations carried out with agricultural machinery, from soil tillage and sowing to fertilisation, pest control treatments, irrigation, and transport to the farm centre. This production is modelled as a irrigated system. Modelled crop duration: 25 years, but the first 3 years are unproductive. Yield: {5} kg/ha.',
                    ],
                    'geographyComment': [
                        'Data on {1} mandarins grove cultivation represent {2} region (southern Italy). Data on fertilisers (based on nitrogen, phosphorus and potassium) refer to Italy (national average). Data on fuel, lubricating oil, anti-cryptogamics, herbicides and applied technologies refer to the plains of southern Italy.',
                        'Data on integrated mandarins grove cultivation represent {1} Italy. Data on fertilisers (based on nitrogen, phosphorus and potassium), fuel, lubricating oil, anti-cryptogamics, herbicides and applied technologies refer to Italy.',
                    ],
                    'includedActivitiesEnd': [
                        'This activity ends after harvesting the mandarins.',
                        'This activity ends after harvesting the oranges.',
                    ],
                    'includedActivitiesStart': [
                        'This activity starts with soil preparation before planting the fruit tree seeds.',
                    ],
                    'modellingAndValidation_extrapolations': [
                        'The agricultural phase of {1} in {2} is derived from {3} data. The analysis of production and cultivated area, and the relative production yield are the result of average data for the time period 2015-2020. Fertilisers application rates (in terms of N, P and K) are derived from {4} Materials such as fuel, lubricating oil, pesticides, herbicides are taken from {5} The market values of the product is an average of the price quotations given in ISMEA database. The emissions due to fertilisers use are accorded to IPCC Report, 2006 and the JRC Report "Zampori and Pant, 2019". The emissions due to pesticides use are calculated according to the OLCA-Pest model (Nemecek et al. 2022). Emissions of heavy metals are estimated according to what is indicated in the SALCA heavy metals model (Freiermuth, 2006), however the data have been regionalised for the Italian territory according to the ESDAC database (Notarnicola et al., 2023). Mandarins production value is the result of {6} average {7} data {8} For the estimation of heavy metals, the SALCA heavy metals model is followed. The erosion and leaching of heavy metals is determined following the identification of local data on land configuration, soil type, the physical and chemical characteristics of the soils, and the activities carried out for the cultivation of citrus. The heavy metals for which emissions have been estimated are: Cd, Cu, Cr, Zn, Ni, Pb, Hg.',
                    ],
                    'technologyComment': [
                        'The reference technology is evaluated using data collected on field. The process describes the average production of mandarins, fresh grade, cultivated in one hectare of land in {1} region (Southern Italy). The activities considered include: fruit tree seedling production for planting, planting of trees and establishment of the orchard, followed by full production {2} grove management. The annual management of the {3} grove includes: soil preparation through rotary cultivation and harrowing by rotary harrow; drip irrigation; {4} fertilisation with {5} nitrogen (as N), {6} phosphorus (as P₂O₅) and {7} potassium (as K₂O), applied by broadcaster; weed and pest control through the application of plant protection products by field sprayer. Finally, the system includes manual pruning and manual harvesting of the fruit and transport of the products to the farm center. The consumption of fuel and lubricating oil are derived from data collected on field. The chemical fertilisers used are N, P2O5 and K2O. The {8} insecticides used are {9}',
                        'The reference technology is evaluated using data collected from statistical sources. The process describes the average production of mandarins, fresh grade, cultivated in one hectare of land in {1} Italy. The activities considered include: fruit tree seedling production for planting, planting of trees and establishment of the orchard, followed by full production clementine grove management. The annual management of the mandarin grove includes: soil preparation through rotary cultivation and harrowing by rotary harrow; drip irrigation; mineral fertilisation with inorganic nitrogen (as N), inorganic phosphorus (as P₂O₅) and inorganic potassium (as K₂O), applied by broadcaster; weed and pest control through the application of plant protection products by field sprayer. Finally, the system includes manual pruning and manual harvesting of the fruit and transport of the products to the farm center. The consumption of fuel and lubricating oil are derived from Ribaudo (2017). The chemical fertilisers used are N, P2O5 and K2O. The herbicides used are Carfentrazone, Diflufenican. The fungicide used is copper oxychloride. The insecticides used are Mancozeb, Pyriproxifen, Phosmet, Azadirachtin, Mineral oil.',
                    ],
                    'timePeriodComment': [
                        'Data were collected in the field between 2020 and 2023. Production per hectare represents the average of the past three years for taking into account the yearly fluctuations of {1} production.',
                    ],
                },
            },
            'minor citrus fruit production': {
                'datasets': 8,
                'isic': '0123:Growing of citrus fruits',
                'fixed_segments': 3,
                'optional_last': True,
                'segment_counts': [3, 4],
                'open_axes': [False, False, False],
                'axis_labels': ['production_system', 'water_regime', 'grade'],
                'optional_label': 'region',
                'optional_vocabulary': ['islands', 'north west', 'south'],
                'vocabulary': [
                    ['conventional', 'integrated', 'organic'],
                    ['irrigated'],
                    ['fresh grade'],
                ],
                'templates': {
                    'generalComment': [
                        'This dataset represents the average production of 1 kg of minor citrus fruits, obtained under {1} cultivation at farm level to produce {2} citrus {3} fresh grade. This cultivation generates one output: minor citrus {4} fresh grade. The dataset includes the production of seedlings, planting trees and establishing of orchard, {5} fertilisers (Nitrogen, Phosphorus, Potassium), and plant protection products (herbicides, fungicides, and insecticides). It encompasses all mechanical operations carried out with agricultural machinery, from soil tillage and sowing to fertilisation, pest control treatments, irrigation, and transport to the farm centre. This production is modelled as a irrigated system. Modelled crop duration: 25 years, but the first 3 years are unproductive. Yield: {6} kg/ha.',
                    ],
                    'geographyComment': [
                        'Data on {1} minor citrus fruits grove cultivation represent {2} region (southern Italy). Data on fertilisers (based on nitrogen, phosphorus and potassium) refer to Italy (national average). Data on fuel, lubricating oil, anti-cryptogamics, herbicides and applied technologies refer to the plains of southern Italy.',
                        'Data on integrated minor citrus fruits grove cultivation represent {1} Italy. Data on fertilisers (based on nitrogen, phosphorus and potassium), fuel, lubricating oil, anti-cryptogamics, herbicides and applied technologies refer to Italy.',
                    ],
                    'includedActivitiesEnd': [
                        'This activity ends after harvesting the minor citrus fruits.',
                        'This activity ends after harvesting the oranges.',
                    ],
                    'includedActivitiesStart': [
                        'This activity starts with soil preparation before planting the fruit tree seeds.',
                    ],
                    'modellingAndValidation_extrapolations': [
                        'The agricultural phase of {1} citrus fruits {2} in {3} is derived from on field data. The analysis of production and cultivated area, and the relative production yield are the result of average data for the time period 2015-2020. Fertilisers application rates (in terms of N, P and K) are derived from the average of data collected on field. Materials such as fuel, lubricating oil, pesticides, herbicides are taken from on field data collection. The market values of the product is an average of the price quotations given in ISMEA database. The emissions due to fertilisers use are accorded to IPCC Report, 2006 and the JRC Report "Zampori and Pant, 2019". The emissions due to pesticides use are calculated according to the OLCA-Pest model (Nemecek et al. 2022). Emissions of heavy metals are estimated according to what is indicated in the SALCA heavy metals model (Freiermuth, 2006), however the data have been regionalised for the Italian territory according to the ESDAC database (Notarnicola et al., 2023). Minor citrus fruits production value is the result of he average of data collected on field. The data on fuel and lubricating oil consumption are representative of the plain cultivation in {4} Region (Southern Italy). For the estimation of heavy metals, the SALCA heavy metals model is followed. The erosion and leaching of heavy metals is determined following the identification of local data on land configuration, soil type, the physical and chemical characteristics of the soils, and the activities carried out for the cultivation of {5} The heavy metals for which emissions have been estimated are: Cd, Cu, Cr, Zn, Ni, Pb, Hg.',
                        'The agricultural phase of integrated minor citrus fruits cultivation in {1} Italy is derived from statistical data. The analysis of production and cultivated area, and the relative production yield are the result of average data for the time period 2015-2020. Fertilisers application rates (in terms of N, P and K) are derived from Ribaudo F. (2017). Materials such as fuel, lubricating oil, pesticides, herbicides are taken from Ribaudo F. (2017) and Guidelines for Integrated Production. (2021). The market values of the product is an average of the price quotations given in ISMEA database. The emissions due to fertilisers use are accorded to IPCC Report, 2006 and the JRC Report "Zampori and Pant, 2019". The emissions due to pesticides use are calculated according to the OLCA-Pest model (Nemecek et al. 2022). Emissions of heavy metals are estimated according to what is indicated in the SALCA heavy metals model (Freiermuth, 2006), however the data have been regionalised for the Italian territory according to the ESDAC database (Notarnicola et al., 2023). Minor citrus fruits production value is the result of the average data from ISTAT, 2021. For the estimation of heavy metals, the SALCA heavy metals model is followed. The erosion and leaching of heavy metals is determined following the identification of local data on land configuration, soil type, the physical and chemical characteristics of the soils, and the activities carried out for the cultivation of citrus. The heavy metals for which emissions have been estimated are: Cd, Cu, Cr, Zn, Ni, Pb, Hg.',
                    ],
                    'technologyComment': [
                        'The reference technology is evaluated using data collected on field. The process describes the average production of {1} citrus fruits, fresh grade, cultivated in one hectare of land in {2} region (Southern Italy). The activities considered include: fruit tree seedling production for planting, planting of trees and establishment of the orchard, followed by full production {3} grove management. The annual management of the {4} grove includes: soil preparation through rotary cultivation and harrowing by rotary harrow; drip irrigation; {5} fertilisation with {6} nitrogen (as N), {7} phosphorus (as P₂O₅) and {8} potassium (as K₂O), applied by broadcaster; weed and pest control through the application of plant protection products by field sprayer. Finally, the system includes manual pruning and manual harvesting of the fruit and transport of the products to the farm center. The consumption of fuel and lubricating oil are derived from data collected on field. The chemical fertilisers used are N, P2O5 and K2O. The {9} insecticides used are {10}',
                        'The reference technology is evaluated using data collected from statistical sources. The process describes the average production of minor citrus fruits, fresh grade, cultivated in one hectare of land in {1} Italy. The activities considered include: fruit tree seedling production for planting, planting of trees and establishment of the orchard, followed by full production clementine grove management. The annual management of the minor citrus fruits grove includes: soil preparation through rotary cultivation and harrowing by rotary harrow; drip irrigation; mineral fertilisation with inorganic nitrogen (as N), inorganic phosphorus (as P₂O₅) and inorganic potassium (as K₂O), applied by broadcaster; weed and pest control through the application of plant protection products by field sprayer. Finally, the system includes manual pruning and manual harvesting of the fruit and transport of the products to the farm center. The consumption of fuel and lubricating oil are derived from Ribaudo (2017). The chemical fertilisers used are N, P2O5 and K2O. The herbicides used are Carfentrazone, Diflufenican. The fungicide used is copper oxychloride. The insecticides used are Mancozeb, Pyriproxifen, Phosmet, Azadirachtin, Mineral oil.',
                    ],
                    'timePeriodComment': [
                        'Data were collected in the field between 2020 and 2023. Production per hectare represents the average of the past three years for taking into account the yearly fluctuations of {1} production.',
                    ],
                },
            },
            'olive production': {
                'datasets': 24,
                'isic': '0126:Growing of oleaginous fruits',
                'fixed_segments': 4,
                'optional_last': True,
                'segment_counts': [4, 5],
                'open_axes': [False, False, False, False],
                'axis_labels': ['production_system', 'water_regime', 'training_system', None],
                'optional_label': 'region',
                'optional_vocabulary': ['centre', 'islands', 'north east', 'north west', 'south'],
                'vocabulary': [
                    ['integrated'],
                    ['irrigated', 'non irrigated'],
                    ['full wind', 'poli cone', 'single cone'],
                    ['hill', 'plain'],
                ],
                'templates': {
                    'generalComment': [
                        'This dataset represents the average production of 1 kg of olives, without distinguishing among different olive cultivars, obtained under integrated cultivation at the farm level in olive orchards in regions in {1} of Italy, representative of typical management conditions in {2} lowland areas. The cultivation is carried out in {3} olive orchards, managed {4} Italian integrated production guidelines, and the system is modelled at the farm gate to produce olives that {5} transported to {6} extraction plants. The dataset includes the production and application of mineral fertilisers {7} and {8} and plant protection products, in accordance with {9} guidelines {10} integrated production. It also encompasses all mechanical operations carried out with agricultural machinery, including pruning, soil management, fertilisation, pest control, harvesting, and transport to olive mill extraction plants. Modelled crop period: {11} years. {12} the first {13} years of the orchard are {14} and orchard decommissioning is not included in the dataset. Yield: {15} kg/ha.',
                    ],
                    'geographyComment': [
                        'Data represent integrated olive cultivation from {1} trees in the regions of Abruzzo, Basilicata, Calabria, Campania, Molise, and Puglia (Italy).',
                        'Data represent integrated olive cultivation from {1} trees in the regions of Emilia-Romagna, Friuli-Venezia Giulia, Trentino-South Tyrol, Veneto (Italy).',
                        'Data represent integrated olive cultivation from {1} trees in the regions of Lazio, Marche, Tuscany (Italy).',
                        'Data represent integrated olive cultivation from {1} trees in the regions of Liguria, Lombardy, Piedmont (Italy).',
                        'Data represent integrated olive cultivation from {1} trees in the regions of {2}',
                    ],
                    'includedActivitiesEnd': [
                        'This activity ends after the harvesting of the olive.',
                    ],
                    'includedActivitiesStart': [
                        'This activity starts with the fruit tree seedling, planting a tree and establishing an orchard.',
                    ],
                    'modellingAndValidation_extrapolations': [
                        'The agricultural phase of olive cultivation in Italy is derived from statistical and literature data adapted and corrected for the reference area covered by this data sheet. The analysis of production, cultivated area, and the relative production yield are the result of average data for the time period 2015–2020 derived from the ISTAT database (ISTAT, 2021). {1} consumption is derived from Ribaudo (2017). Fertilisers and plant protection quantity derive from Italian guidelines of integrated production. The emissions due to fertilisers use are calculated according to IPCC Report (2006) and Zampori and Pant (2019). The emissions due to pesticides use are calculated according to the OLCA-Pest model (Nemecek et al. 2022). Emissions of heavy metals are estimated according to what is indicated in the SALCA heavy metals model (Freiermuth, 2006), however the data have been regionalised for the Italian territory according to Notarnicola et al. (2023). The erosion and leaching of heavy metals is determined following the identification of local data on land configuration, soil type, the physical and chemical characteristics of the soils, and the activities carried out for the cultivation of olive trees, based on JRC data from the ESDAC and LUCAS databases.',
                    ],
                    'technologyComment': [
                        'The reference technology is evaluated using literature data. The process describes the average production of olives cultivated in Italian regions adopting an integrated production system. The activities considered include: soil management under rainfed (no irrigation) conditions; fertilisation and pest control; pruning operations; harvesting; and transport operations. Field operations include the use of agricultural machinery for pruning {1} of pruning residues, soil tillage and harrowing, mechanical harvesting and application of plant protection products by field sprayer. The consumption of fuel is derived from literature data (Ribaudo, 2017). The fertilisers (N, P2O5 and K2O) and the plant protection products are applied according to Italian guidelines on integrated production. Transport is carried out by lorry and trucks.',
                        'The reference technology is evaluated using literature data. The process describes the average production of olives cultivated in Italian regions adopting an integrated production system. The activities considered include: soil management under {1} conditions; fertilisation and pest control; pruning operations; harvesting; and transport operations. Field operations comprise soil tillage using a reversible mouldboard plough and either a disc harrow or {2} cultivator; winter pruning performed with pneumatic shears; shredding of pruning residues using a branch shredder; localized weed control with an inter-row sprayer; fertiliser application with a centrifugal spreader equipped with oscillating tube; and phytosanitary treatments against olive {3} and olive {4} using a mist sprayer. Harvesting is carried out {5} mechanical harvesting. {6} The consumption of fuel is derived from literature data (Ribaudo, 2017). The fertilisers (N, P2O5 and K2O) and the plant protection products are applied according to Italian guidelines on integrated production. Transport is carried out by lorry and trucks.',
                    ],
                    'timePeriodComment': [
                        'The statistical analysis is conducted over a six-year time period (2015 - 2020), so as to consider average values that take into account changes in agricultural yield.',
                    ],
                },
            },
            'orange production': {
                'datasets': 8,
                'isic': '0123:Growing of citrus fruits',
                'fixed_segments': 3,
                'optional_last': True,
                'segment_counts': [3, 4],
                'open_axes': [False, False, False],
                'axis_labels': ['production_system', 'water_regime', 'grade'],
                'optional_label': 'region',
                'optional_vocabulary': ['centre', 'islands', 'north west', 'south'],
                'vocabulary': [
                    ['conventional', 'integrated', 'organic'],
                    ['irrigated'],
                    ['fresh grade'],
                ],
                'templates': {
                    'generalComment': [
                        'This dataset represents the average production of 1 kg of {1} obtained under {2} cultivation at farm level to produce orange, fresh grade. This cultivation generates one output: {3} fresh grade. The dataset includes the production of seedlings, planting trees and establishing of orchard, {4} fertilisers (Nitrogen, Phosphorus, Potassium), and plant protection products (herbicides, fungicides, and insecticides). It encompasses all mechanical operations carried out with agricultural machinery, from soil tillage and sowing to fertilisation, pest control treatments, irrigation, and transport to the farm centre. This production is modelled as a irrigated system. Modelled crop duration: 25 years, but the first 3 years are unproductive. Yield: {5} kg/ha.',
                    ],
                    'geographyComment': [
                        'Data on {1} orange grove cultivation represent {2} region (southern Italy). Data on fertilisers (based on nitrogen, phosphorus and potassium) refer to Italy (national average). Data on fuel, lubricating oil, anti-cryptogamics, herbicides and applied technologies refer to the plains of southern Italy.',
                        'Data on integrated orange grove cultivation represent {1} Italy. Data on fertilisers (based on nitrogen, phosphorus and potassium), fuel, lubricating oil, anti-cryptogamics, herbicides and applied technologies refer to Italy.',
                    ],
                    'includedActivitiesEnd': [
                        'This activity ends after harvesting the {1}',
                    ],
                    'includedActivitiesStart': [
                        'This activity starts with soil preparation before planting the fruit tree seeds.',
                    ],
                    'modellingAndValidation_extrapolations': [
                        'The agricultural phase of {1} orange {2} in {3} is derived from {4} data. The analysis of production and cultivated area, and the relative production yield are the result of average data for the time period 2015-2020. Fertilisers application rates (in terms of N, P and K) are derived from {5} Materials such as fuel, lubricating oil, pesticides, herbicides are taken from {6} The market values of the product is an average of the price quotations given in ISMEA database. The emissions due to fertilisers use are accorded to IPCC Report, 2006 and the JRC Report "Zampori and Pant, 2019". The emissions due to pesticides use are calculated according to the OLCA-Pest model (Nemecek et al. 2022). Emissions of heavy metals are estimated according to what is indicated in the SALCA heavy metals model (Freiermuth, 2006), however the data have been regionalised for the Italian territory according to the ESDAC database (Notarnicola et al., 2023). Oranges production value is the result of {7} average {8} data {9} For the estimation of heavy metals, the SALCA heavy metals model is followed. The erosion and leaching of heavy metals is determined following the identification of local data on land configuration, soil type, the physical and chemical characteristics of the soils, and the activities carried out for the cultivation of citrus. The heavy metals for which emissions have been estimated are: Cd, Cu, Cr, Zn, Ni, Pb, Hg.',
                        'The agricultural phase of integrated orange cultivation in {1} Italy is derived from statistical data. The analysis of production and cultivated area, and the relative production yield are the result of average data for the time period 2015-2020. Fertilisers application rates (in terms of N, P and K) are derived from Ribaudo F. (2017). Materials such as fuel, lubricating oil, pesticides, herbicides are taken from Ribaudo F. (2017) and Guidelines for Integrated Production. (2021). The market values of the product is an average of the price quotations given in ISMEA database. The emissions due to fertilisers use are accorded to IPCC Report, 2006 and the JRC Report "Zampori and Pant, 2019". The emissions due to pesticides use are calculated according to the OLCA-Pest model (Nemecek et al. 2022). Emissions of heavy metals are estimated according to what is indicated in the SALCA heavy metals model (Freiermuth, 2006), however the data have been regionalised for the Italian territory according to the ESDAC database (Notarnicola et al., 2023). Oranges production value is the result of the average data from ISTAT, 2021. For the estimation of heavy metals, the SALCA heavy metals model is followed. The erosion and leaching of heavy metals is determined following the identification of local data on land configuration, soil type, the physical and chemical characteristics of the soils, and the activities carried out for the cultivation of citrus. The heavy metals for which emissions have been estimated are: Cd, Cu, Cr, Zn, Ni, Pb, Hg.',
                    ],
                    'technologyComment': [
                        'The reference technology is evaluated using data collected {1} The process describes the average production of {2} fresh grade, cultivated in one hectare of land in {3} The activities considered include: fruit tree seedling production for planting, planting of trees and establishment of the orchard, followed by full production {4} grove management. The annual management of the orange grove includes: soil preparation through rotary cultivation and harrowing by rotary harrow; drip irrigation; {5} fertilisation with {6} nitrogen (as N), {7} phosphorus (as P₂O₅) and {8} potassium (as K₂O), applied by broadcaster; weed and pest control through the application of plant protection products by field sprayer. Finally, the system includes manual pruning and manual harvesting of the fruit and transport of the products to the farm center. The consumption of fuel and lubricating oil are derived from {9} The chemical fertilisers used are N, P2O5 and K2O. The {10} fungicide used is copper oxychloride. The insecticides used are {11}',
                    ],
                    'timePeriodComment': [
                        'Data were collected in the field between 2020 and 2023. Production per hectare represents the average of the past three years for taking into account the yearly fluctuations of {1} production.',
                    ],
                },
            },
            'wheat grain production': {
                'datasets': 21,
                'isic': '0111:Growing of cereals (except rice), leguminous crops and oil seeds',
                'fixed_segments': 3,
                'optional_last': True,
                'segment_counts': [3, 4],
                'open_axes': [False, False, False],
                'axis_labels': [None, 'production_system', 'water_regime'],
                'optional_label': 'region',
                'optional_vocabulary': ['centre', 'islands', 'north east', 'north west', 'south'],
                'vocabulary': [
                    ['common', 'durum'],
                    ['conventional', 'organic'],
                    ['non irrigated'],
                ],
                'templates': {
                    'allocationComment': [
                        'For the allocation of wheat, a factor of {1} is used (while for straw the factor is {2} derived from an economic-physical allocation. Wheat and straw yields are reported in "Notarnicola et al., 2001". The average price of {3} wheat {4} €/t) and straw (50 €/t) over the reference period {5} is reported in "Ribaudo, 2017". Prontuario di Agricoltura, Hoepli: https://www.hoepli.it/libro/prontuario-di-agricoltura/9788820376628.html.',
                    ],
                    'generalComment': [
                        'This dataset represents the average production of 1 kg of {1} wheat (Triticum {2} obtained under {3} cultivation at farm level to produce grain that will later be transported to storage or processing plants. This cultivation generates two outputs: {4} wheat grain as the reference product and straw as a by-product. The dataset includes the production and application of seeds, {5} fertilisers {6} It encompasses all mechanical operations carried out with agricultural machinery, from soil tillage and sowing to fertilisation, pest control treatments, harvesting, and transport to the farm centre. This production is modelled as a rainfed system (no irrigation). Yield: {7} kg/ha.',
                        'This dataset represents the average production of 1 kg of durum wheat (Triticum durum), obtained under organic cultivation at farm level to produce grain that will later be transported to storage or processing plants. This cultivation generates two outputs: durum wheat grain as the reference product and straw as a by-product. The dataset includes the production and application of seeds, organic fertilisers (solid manure). It encompasses all mechanical operations carried out with agricultural machinery, from soil tillage and sowing to fertilisation, pest control treatments, harvesting, and transport to the farm centre. This production is modelled as a rainfed system (no irrigation). Yield: {1} kg/ha.',
                    ],
                    'geographyComment': [
                        'Data on {1} wheat cultivation represent {2} Italy {3} of national production considered). Data on fertilisers (based on nitrogen, phosphorus and potassium) refer to {4} Italy (national average). Data on fuel, lubricating oil, anti-cryptogamics, herbicides and applied technologies refer to the {5} and the hills of {6} Italy and are appropriately adapted to the entire territory.',
                        'Data on organic {1} wheat cultivation represent {2} Italy {3} of national production considered). Data on fertilisers (based on nitrogen, phosphorus and potassium) refer to {4} Italy (national average). Data on fuel, lubricating oil, manure and applied technologies refer to hills of Central Italy and are appropriately adapted to the entire territory.',
                    ],
                    'includedActivitiesEnd': [
                        'This activity ends after harvesting durum wheat grain.',
                    ],
                    'includedActivitiesStart': [
                        'This activity starts with soil preparation before sowing the durum wheat.',
                    ],
                    'modellingAndValidation_extrapolations': [
                        'The agricultural {1} of conventional {2} wheat cultivation in {3} Italy is derived from statistical and literature data adapted and corrected for the reference area covered by this data sheet. The analysis of production and cultivated area, and the relative production yield are the result of average data for the time period 2015-2020 derived from the ISTAT database. Fertilisers application rates (in terms of N, P and K) are derived from the average of the integrated production specifications of the regions where conventional {4} wheat production occurs, where the maximum legal quantity applicable for one hectare of land cultivated with conventional {5} wheat was considered. Materials such as fuel, lubricating oil, pesticides, herbicides are taken from the data in the Agricultural Handbook "Ribaudo, 2017". The market values of the product and by-product are an average of the price quotations given in the Ribaudo. The quantity of by-product was determined according to {6} The emissions due to fertilisers use are accorded to IPCC Report, 2006 and the JRC Report "Zampori and Pant, 2019". The emissions due to pesticides use are calculated according to the OLCA-Pest model (Nemecek et al. 2022). Emissions of heavy metals are estimated according to what is indicated in the SALCA heavy metals model (Freiermuth, 2006), however the data have been regionalised for the Italian territory according to the ESDAC database (Notarnicola et al., 2023). {7} wheat production values are the result of combining statistical data from several databases relating to Italy. A cut-off of 1% was set, {8} regions with a percentage contribution in terms of production {9} were considered. The data are deduced as a {10} average in {11} Italy in which the regions where conventional {12} wheat is grown were taken into account (in alphabetical order, {13} representing the {14} production of conventional {15} The data on fuel and lubricating oil consumption are representative of the {16} and the hills of {17} Italy, however, corrections have been used to represent the national territory. The ISTAT data indicate that the configuration of the national territory is {18} hilly and {19} flat. Consequently, a 10% increase in fuel and lubricating oil consumption was assumed as the altitude zone increases. For the estimation of heavy metals, the SALCA heavy metals model is followed. The erosion and leaching of heavy metals is determined following the identification of local data on land configuration, soil type, the physical and chemical characteristics of the soils, and the activities carried out for the cultivation of {20} wheat. The heavy metals for which emissions have been estimated are: Cd, Cu, Cr, Zn, Ni, Pb, Hg.',
                        'The agricultural stage of organic {1} wheat cultivation in {2} Italy is derived from statistical and literature data adapted and corrected for the reference area covered by this data sheet. The analysis of production and cultivated area, and the relative production yield are the result of average data for the time period {3} derived from the SINAB database. Fertilisers application rates (in terms of N, P and K) are derived from the average of the integrated production specifications of the regions in which organic {4} wheat {5} where the maximum legal quantity applicable for one hectare of land cultivated with organic {6} wheat was considered. Materials such as fuel, lubricating {7} are taken from the data in the Agricultural Handbook "Ribaudo, 2017". The market values of the product and by-product are an average of the price quotations given in the Ribaudo. The quantity of by-product was determined according to {8} Transport of the manure was assumed equal to 10 km. The emissions due to fertilisers use are accorded to IPCC Report, 2006 and the JRC Report "Zampori and Pant, 2019". Emissions of heavy metals are estimated according to what is indicated in the SALCA heavy metals model (Freiermuth, 2006), however the data have been regionalised for the Italian territory according to the ESDAC database (Notarnicola et al., 2023). Organic {9} wheat production values are the result of combining statistical data from several databases relating to Italy. The data are deduced as a national average in {10} Italy {11} which the regions where organic {12} wheat is grown were taken into account (in alphabetical order, {13} representing the {14} production of {15} The data on fuel and lubricating oil consumption are representative of the {16} central {17} however, corrections have been used to represent the national territory. The {18} ISTAT data indicate that the configuration of the national territory is {19} hilly and {20} flat. Consequently, a 10% increase in fuel and lubricating oil consumption was assumed as the altitude zone increases. For the estimation of heavy metals, the SALCA heavy metals model is followed. The erosion and leaching of heavy metals is determined following the identification of local data on land configuration, soil type, the physical and chemical characteristics of the soils, and the activities carried out for the cultivation of {21} wheat. The heavy metals for which emissions have been estimated are: Cd, Cu, Cr, Zn, Ni, Pb, Hg. The emissions due to pesticides use are calculated according to the OLCA-Pest model (Nemecek et al. 2022).',
                    ],
                    'technologyComment': [
                        'The reference technology was evaluated using literature data. The process describes the average production of common wheat cultivated in {1} Italy. The activities considered include: soil preparation (light ploughing and harrowing), soil fertilisation and refinement (chemical fertiliser distribution, harrowing), actual sowing, crop care (fertiliser distribution, weed control and cryptogame control), harvesting and product storage (harvesting, bulk grain storage: mechanical loading, transport, mechanical unloading) and, finally, straw activities. The consumption of fuel and lubricating oil was derived from data in the Prontuario di Agricoltura (Ribaudo, 2017), however, a 10% increase in consumption was assumed as altitude zones increased. The identification of altimetric zones for Italy was possible through ISTAT data (plain, hills, mountains). The chemical fertilisers used are N, P2O5 and K2O. The herbicides used are: {2}',
                        'The reference technology was evaluated using literature data. The process describes the average production of organic common wheat cultivated in one hectare of land in Italy. The activities considered include: soil preparation (manure spreading, light ploughing, grubbing and harrowing), actual sowing, crop care (weed control), harvesting and storage (combine harvesting, bulk grain storage: loading, transport, unloading) and, finally, straw harvesting and storage (straw and stubble burying). The consumption of fuel and lubricating oil was derived from data in the Prontuario di Agricoltura (Ribaudo, 2017), however, a 10% increase in consumption was assumed as altitude zones increased. The identification of altimetric zones for {1} Italy was possible through ISTAT data (plain, hills, mountains). The organic fertilisers consumed consist of N, P2O5 and K2O applied through cattle manure.',
                        'The reference technology is evaluated using literature data. The process describes the average production of durum wheat cultivated in {1} Italy. The activities considered include: soil preparation (light ploughing and harrowing), soil fertilisation and refinement (chemical fertiliser distribution, harrowing), actual sowing, crop care (fertiliser distribution, weeding and cryptogame control) and, finally, harvesting and storage of the product (harvesting, bulk grain storage: mechanical loading, transport, mechanical unloading). The consumption of fuel and lubricating oil are derived from data in the Prontuario di Agricoltura (Ribaudo, 2017), however, a 10% increase in consumption was assumed as altitude zones increased. The identification of altimetric zones for Italy was possible through ISTAT data (plain, hills, mountains). The chemical fertilisers used are N, P2O5 and K2O. The herbicides used are: {2} iodosulfuron. {3}',
                        'The reference technology was evaluated using literature data. The process describes the average production of organic durum wheat cultivated in one hectare of land in Italy. The activities considered include: soil preparation (manure spreading, light ploughing, grubbing and harrowing), actual sowing, crop care (weed control), harvesting and storage (combine harvesting, bulk grain storage: loading, transport, unloading) and, finally, straw harvesting and storage (straw and stubble burying). These activities do not relate directly to the cultivation of organic durum wheat, but are similar processes related to the cultivation of one hectare of organic common wheat. The consumption of fuel and lubricating oil was derived from data in the Prontuario di Agricoltura (Ribaudo, 2017) (but for the similar cultivation of organic common wheat), however, a 10% increase in consumption was assumed as altitude zones increased. The identification of altimetric zones for {1} Italy was possible through ISTAT data (plain, hills, mountains). The organic fertilisers consumed are N, P2O5 and K2O applied through cattle manure.',
                    ],
                    'timePeriodComment': [
                        'The statistical analysis is conducted over a {1} time period {2} - {3} so as to consider average values that take into account changes in agricultural yield.',
                    ],
                },
            },
        },
    },
    '_prof': {
        'datasets': 6,
        'constants': {
            'dataEntryBy_isActiveAuthor': 'True',
            'dataGeneratorAndPublication_accessRestrictedTo': 'Licensees',
            'specialActivityType': 'ordinary transforming activity',
            'technologyLevel': 'Current',
        },
        'always_empty': (
            'synonyms',
            'tags',
            'dataEntryBy_personName',
            'dataGeneratorAndPublication_dataPublishedIn',
            'dataGeneratorAndPublication_pageNumbers',
            'dataGeneratorAndPublication_personName',
            'dataGeneratorAndPublication_publishedSourceId',
            'modellingAndValidation_percent',
            'fileAttributes_majorRelease',
            'fileAttributes_minorRelease',
            'fileAttributes_majorRevision',
            'fileAttributes_minorRevision',
        ),
        'regions': (),
        'families': {
            'grape production': {
                'datasets': 6,
                'isic': '0121:Growing of grapes',
                'fixed_segments': 4,
                'optional_last': False,
                'segment_counts': [4],
                'open_axes': [True, False, False, True],
                'axis_labels': [None, 'production_system', 'water_regime', 'region'],
                'optional_label': None,
                'optional_vocabulary': [],
                'vocabulary': [
                    ['asti', 'brunello montalcino', 'grillo', 'lago caldaro', 'puglia', "trebbiano d'abruzzo"],
                    ['conventional'],
                    ['irrigated'],
                    ['centre', 'islands', 'north east', 'north west', 'south'],
                ],
                'templates': {
                    'generalComment': [
                        'This dataset represents the average production of 1 kg of {1} grapes produced in the {2} region (Italy), obtained under conventional cultivation at farm level to produce grapes that will later be transported to processing plants. The dataset includes the application and field distribution of mineral fertilizers (Nitrogen, Potassium, Phosphorus), plant protection treatments (fungicides and cryptogam control), and the consumption of {3} and auxiliary materials. It encompasses all mechanical operations carried out with agricultural machinery, from {4} to fertilization, pest control {5} and transports. Modeled crop duration 25 years, but the first 3 years are unproductive. Yield: {6} kg/ha.',
                    ],
                    'geographyComment': [
                        'The inventory is modeled for the Italian region of {1}',
                    ],
                    'includedActivitiesEnd': [
                        'This activity ends after the harvesting of the grapes.',
                    ],
                    'includedActivitiesStart': [
                        'This activity starts with soil preparation before the harvesting of the grapes.',
                    ],
                    'modellingAndValidation_extrapolations': [
                        'The agricultural phase of grape cultivation in the region {1} of Italy is derived from statistical and literature data adapted and corrected for the reference area covered by this data sheet. The analysis of production and cultivated area, and the relative production yield are the result of average data for the time period 2015-2020 derived from the ISTAT database. Fertiliser application rates (in terms of N, P and K) derived from the average of the integrated production specifications of the regions where {2} production occurs, where the maximum legal quantity applicable for one hectare of land cultivated with grapes was considered. Materials such as fuel and lubricating oil and fertilisers are taken from the data in the Agricultural Handbook "Ribaudo, 2017". The emissions due to fertilisers use are according to the IPCC Report, 2006 and the JRC Report "Zampori et al, 2019". The emissions due to the use of pesticides and insecticides follow "Zampori et al, 2019". Emissions of heavy metals are estimated according to what is indicated in the SALCA heavy metals model (Freiermuth, 2006), however the data have been regionalised for the Italian territory according to the ESDAC database (Notarnicola et al., 2023). {3} Grapes production values are the result of combining data from Agricultural Handbook "Ribaudo, 2017" and Production Regulations of {4} region, 2022). The data on fuel, lubricating oil consumption, fertilisers and cryptogams are representative of the lowlands of {5} Italy. For the estimation of heavy metals, the SALCA heavy metals model is followed. The erosion and leaching of heavy metals is determined following the identification of local data on land configuration, soil type, the physical and chemical characteristics of the soils, and the activities carried out for the cultivation of {6} grapes. The heavy metals for which emissions have been estimated are: Cd, Cu, Cr, Zn, Ni, Pb, Hg.',
                    ],
                    'technologyComment': [
                        'The reference technology was evaluated using literature data. The process describes the average production of {1} grapes in one hectare of land in Italy. The activities considered include: application and distribution of fertilisers (based on nitrogen, phosphorus and potassium), parasites treatment (cryptogam control) as well as the consumption of fuel for Weeding, Combating cryptogams, the usage of a Rotary Hoe and transports. Their usage is derived from data in the Production Regulations of {2} region, 2022) and the Agricultural Handbook (Ribaudo, 2017). The chemical fertilisers include: N, P205 and K2O. The fungicides used are: Bordeaux Mixture, Wettable Sulphur and Pulverulent Sulphur. Sulphur is used as both pesticide and fungicide.',
                    ],
                    'timePeriodComment': [
                        'The statistical analysis is conducted over a six-year time period (2015 - 2020), so as to consider average values that take into account changes in agricultural yield.',
                    ],
                },
            },
        },
    },
    'cashmere': {
        'datasets': 1,
        'constants': {
            'technologyLevel': 'Current',
        },
        'always_empty': (
            'synonyms',
        ),
        'regions': (),
        'families': {
            'cashmere goat production': {
                'datasets': 1,
                'isic': None,
                'fixed_segments': 1,
                'optional_last': False,
                'segment_counts': [1],
                'open_axes': [False],
                'axis_labels': [None],
                'optional_label': None,
                'optional_vocabulary': [],
                'vocabulary': [
                    ['farmed'],
                ],
                'templates': {
                },
            },
        },
    },
    'test': {
        'datasets': 4,
        'constants': {
            'dataEntryBy_isActiveAuthor': 'True',
            'dataGeneratorAndPublication_accessRestrictedTo': 'Licensees',
            'specialActivityType': 'ordinary transforming activity',
            'technologyLevel': 'Current',
        },
        'always_empty': (
            'synonyms',
            'tags',
            'dataEntryBy_personName',
            'dataGeneratorAndPublication_dataPublishedIn',
            'dataGeneratorAndPublication_pageNumbers',
            'dataGeneratorAndPublication_personName',
            'dataGeneratorAndPublication_publishedSourceId',
            'modellingAndValidation_percent',
            'fileAttributes_majorRelease',
            'fileAttributes_minorRelease',
            'fileAttributes_majorRevision',
            'fileAttributes_minorRevision',
        ),
        'regions': (),
        'families': {
            'orange production': {
                'datasets': 1,
                'isic': '0123:Growing of citrus fruits',
                'fixed_segments': 4,
                'optional_last': False,
                'segment_counts': [4],
                'open_axes': [False, False, False, False],
                'axis_labels': ['production_system', 'water_regime', 'grade', None],
                'optional_label': None,
                'optional_vocabulary': [],
                'vocabulary': [
                    ['integrated'],
                    ['irrigated'],
                    ['fresh grade'],
                    ['south'],
                ],
                'templates': {
                },
            },
        },
    },
}
